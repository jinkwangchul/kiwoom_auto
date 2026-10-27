# -*- coding: utf-8 -*-
"""Independent entry and replay flow for indicator signal Validation V5."""

from __future__ import annotations

from indicator_follow_signal_validation_perf_trace import (
    ENABLED as _PERF_ENABLED, stage as _perf_stage, emit as _perf_emit,
    phase as _perf_phase,
)

from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import pickle
from queue import Full, Queue
import re
import tempfile
import time
from threading import Event, Lock, Thread
from threading import Thread as _DiagnosticThread
from sor_m3_operational_evidence import build_v5_records
import sor_m3_native_observation as _m3_observation
import weakref

from PyQt5 import sip
from PyQt5.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import QDialog, QWidget

from candle_timeframe_aggregation import SEOUL_TIMEZONE, SUPPORTED_BAR_MINUTES, candle_market_datetime
from kiwoom_candle_adapter import normalize_opt10080_rows
from gui_toast import show_toast
from gui_stock_data import (
    PROJECT_ROOT, STOCK_LIBRARY_READY, load_stock_library_snapshot, read_base_stocks,
)
from gui_stock_library_browser import normalize_browser_code
from gui_auto_trade_integrity import inspect_stock_review_state
from routine_instance_registry import load_persisted_routine_instances
from runtime_io import read_json_dict
from stock_repository import StockRepository, safe_stock_folder_name
from stock_market_source_plan import (
    NATIVE_CLOSING_PRICE_SPECIAL, initialize_from_verified_library, native_bar_end,
    read_stock_market_source_plan,
    source_for_time, split_native_closing_price_candles,
)
from gui_indicator_follow_signal_validation_v5_window import (
    FAMILY_PRICE_COMPARISON,
    IndicatorFollowSignalValidationV5Window,
    _build_validation_projection_data,
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
from indicator_follow_validation_v5_sor_tail_bridge import apply_verified_sor_tail
from candle_manager import DEFAULT_CANDLES_MAX_COUNT
from engines.indicator_engine import indicator_history_target_bars_from_rules
from gui_indicator_follow_validation_host import IndicatorFollowValidationHost
from gui_indicator_follow_validation_stock_picker import IndicatorFollowValidationStockPicker
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

_SOR_AL_M1_DIAGNOSTIC_PATH = (
    Path(__file__).resolve().parent / "runtime" / "diagnostics" / "v5_sor_al_m1.jsonl"
)
_SOR_AL_M1_DIAGNOSTIC_LOCK = Lock()
_V5_MARKING_DIAGNOSTIC_PATH = (
    Path(__file__).resolve().parent / "runtime" / "diagnostics" / "v5_marking_signal.jsonl"
)
_V5_M3_OPERATIONAL_PATH = (
    Path(__file__).resolve().parent / "runtime" / "diagnostics" / "v5_sor_m3_operational.jsonl"
)
_V5_DIRECT_OPERATIONAL_PATH = (
    Path(__file__).resolve().parent / "runtime" / "diagnostics" / "v5_sor_direct_operational.jsonl"
)
_V5_MARKING_DIAGNOSTIC_QUEUE = Queue(maxsize=256)
_V5_MARKING_DIAGNOSTIC_LOCK = Lock()
_V5_MARKING_DIAGNOSTIC_STARTED = False
_V5_MARKING_DIAGNOSTIC_MAX_BYTES = 4 * 1024 * 1024


def _queue_m3_native_observation(path: Path, line: str) -> bool:
    global _V5_MARKING_DIAGNOSTIC_STARTED
    try:
        with _V5_MARKING_DIAGNOSTIC_LOCK:
            if not _V5_MARKING_DIAGNOSTIC_STARTED:
                _DiagnosticThread(target=_write_v5_marking_diagnostics,
                                  name="v5-marking-diagnostic", daemon=True).start()
                _V5_MARKING_DIAGNOSTIC_STARTED = True
        _V5_MARKING_DIAGNOSTIC_QUEUE.put_nowait((path, line))
        return True
    except (Full, RuntimeError, OSError):
        return False


_m3_observation.set_sink(_queue_m3_native_observation)


def _queue_v5_m3_evidence(lines: str, *, timeframe: str = "M3") -> bool:
    global _V5_MARKING_DIAGNOSTIC_STARTED
    try:
        if timeframe not in {f"M{n}" for n in SUPPORTED_BAR_MINUTES}:
            return False
        with _V5_MARKING_DIAGNOSTIC_LOCK:
            if not _V5_MARKING_DIAGNOSTIC_STARTED:
                _DiagnosticThread(target=_write_v5_marking_diagnostics,
                                  name="v5-marking-diagnostic", daemon=True).start()
                _V5_MARKING_DIAGNOSTIC_STARTED = True
        path = (_V5_M3_OPERATIONAL_PATH if timeframe == "M3"
                else _V5_DIRECT_OPERATIONAL_PATH)
        _V5_MARKING_DIAGNOSTIC_QUEUE.put_nowait((path, lines))
        return True
    except (Full, RuntimeError, OSError):
        return False


def _write_v5_marking_diagnostics() -> None:
    while True:
        path, line = _V5_MARKING_DIAGNOSTIC_QUEUE.get()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                size = 0
            if size + len(line) > _V5_MARKING_DIAGNOSTIC_MAX_BYTES:
                if path == _m3_observation.PATH:
                    _m3_observation.note_file_drop()
                continue
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
        except Exception:
            if path == _m3_observation.PATH:
                _m3_observation.note_file_drop()
            pass  # Diagnostics cannot change marking behavior.
        finally:
            _V5_MARKING_DIAGNOSTIC_QUEUE.task_done()


def _trace_v5_marking(event: str, window_key: object, generation: object,
                      request_generation: object, **fields: object) -> None:
    if event not in {"marking_requested", "marking_calculated", "marking_failed",
                     "marking_published", "marking_stale"}:
        return
    record = {"time": datetime.now(SEOUL_TIMEZONE).isoformat(),
              "pid": os.getpid(), "event": event}
    for name, value in (("window_key", window_key), ("generation", generation),
                        ("request_generation", request_generation),
                        *((name, fields.get(name)) for name in
                          ("calculation_count", "display_count", "entry_count"))):
        if type(value) is int and 0 <= value <= 1_000_000:
            record[name] = value
    reason = fields.get("reason")
    if reason is not None:
        record["reason"] = (reason if type(reason) is str and reason in {
            "MARKING_SOURCE_UNAVAILABLE", "MARKING_TIMEFRAME_MISMATCH",
            "MARKING_PREMARKET_SOURCE_NOT_VERIFIED",
            "SOR_VERIFIED_BLOCKED:MARKING_SOURCE_CHANGED",
            "SOR_M1_BLOCKED:SOURCE_UNAVAILABLE",
        } else "UNCLASSIFIED")
    if event == "marking_failed":
        phase = fields.get("phase")
        if phase is not None:
            record["phase"] = (phase if type(phase) is str and phase in {
                "precheck", "worker", "thread_start", "publication",
            } else "UNCLASSIFIED")
        exception_type = fields.get("exception_type")
        if exception_type is not None:
            record["exception_type"] = (exception_type if type(exception_type) is str
                                        and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}",
                                                         exception_type)
                                        else "UNCLASSIFIED")
    line = json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n"
    global _V5_MARKING_DIAGNOSTIC_STARTED
    try:
        with _V5_MARKING_DIAGNOSTIC_LOCK:
            if not _V5_MARKING_DIAGNOSTIC_STARTED:
                _DiagnosticThread(target=_write_v5_marking_diagnostics,
                                  name="v5-marking-diagnostic", daemon=True).start()
                _V5_MARKING_DIAGNOSTIC_STARTED = True
        _V5_MARKING_DIAGNOSTIC_QUEUE.put_nowait((_V5_MARKING_DIAGNOSTIC_PATH, line))
    except (Full, RuntimeError, OSError):
        pass  # A full or unavailable diagnostic sink cannot affect marking.


def _trace_sor_al_m1(event: str, code: str, **fields: object) -> None:
    """Write bounded M1 lifecycle evidence without broker payloads or session IDs."""
    if not isinstance(code, str) or (code and not re.fullmatch(r"[0-9]{6}", code)):
        return
    record = {
        "time": datetime.now(SEOUL_TIMEZONE).isoformat(),
        "pid": os.getpid(), "event": event, "code": code,
    }
    for key in ("attempt", "count", "required_count", "connection_epoch",
                "received_count", "installed_count", "persisted_count"):
        value = fields.get(key)
        if type(value) is int and value >= 0:
            record[key] = value
    for key in ("response_ok", "install_ok", "installed", "source_match",
                "epoch_match", "persisted", "cache_lease_present"):
        if type(fields.get(key)) is bool:
            record[key] = fields[key]
    reason = fields.get("reason")
    if reason is not None:
        record["reason"] = (reason if isinstance(reason, str) and re.fullmatch(
            r"[A-Z][A-Z0-9_]{0,79}", reason) else "UNCLASSIFIED")
    last_minute = fields.get("last_minute")
    if isinstance(last_minute, str) and re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}\+09:00", last_minute):
        record["last_minute"] = last_minute
    try:
        with _SOR_AL_M1_DIAGNOSTIC_LOCK:
            _SOR_AL_M1_DIAGNOSTIC_PATH.parent.mkdir(parents=True, exist_ok=True)
            with _SOR_AL_M1_DIAGNOSTIC_PATH.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        pass  # Diagnostics cannot change acquisition behavior.
from indicator_follow_signal_validation_projection import (
    IndicatorFollowSignalValidationApplyPayload,
    IndicatorFollowSignalValidationRunRequest,
    IndicatorFollowSignalValidationSeed,
    build_validation_average_price_context,
)
from indicator_follow_signal_validation_visualization import (
    FAMILY_MACD_SIGNAL,
    FAMILY_OCR_OSC,
    FAMILY_RSI,
    ValidationIndicatorSeriesCache,
    active_filter_identities_for_entry,
    build_validation_indicator_cache,
    build_validation_filter_universe,
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


DEFAULT_SIGNAL_VALIDATION_HISTORICAL_COUNT = 5_000
MAX_SIGNAL_VALIDATION_HISTORICAL_COUNT = 5_000
_HISTORY_EXTENSION_STEP = 500
_PERSISTENT_REFRESH_PROBE_COUNT = 300
_LIVE_MINUTE_TIMEFRAMES = frozenset(
    {"M1", "M3", "M5", "M10", "M15", "M30", "M60", "M120", "M240"}
)
_LIVE_CALC_ACCEPTED_TIMEFRAMES = _LIVE_MINUTE_TIMEFRAMES | {
    "D1", "W1", "MO1", "Y1"
}
_V5_SOR_FORMING_SOURCES = frozenset({
    "BROKER_AL_TR_FORMING_M1", "BROKER_AL_PARITY_NATIVE_FORMING_M1",
})
_SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS: dict[
    tuple[int, str, str, str], dict[str, object]
] = {}
_OWNER_FLOW_ATTRIBUTE = "_indicator_follow_signal_validation_v5_flow"
_HISTORY_CACHE_UNSET = object()
_CACHE_LEASE_UNSET = object()


def _extend_prepend_indicator_cache(
    previous: object,
    candles: object,
    source_candles: object,
    cache_key: tuple[object, ...],
    rules: dict[str, object],
    descriptors: tuple[object, ...],
    cancel_event: Event,
) -> ValidationIndicatorSeriesCache | None:
    """Reuse only causal window tails after proving an exact worker-side prepend."""
    if (cancel_event.is_set() or not isinstance(previous, tuple)
            or len(previous) != 3 or not isinstance(candles, list)
            or not isinstance(source_candles, list) or not descriptors):
        return None
    old_key, old_cache, old_candles = previous
    if (not isinstance(old_key, tuple) or len(old_key) != 4
            or not isinstance(old_cache, ValidationIndicatorSeriesCache)
            or not isinstance(old_candles, tuple)
            or len(old_candles) != old_cache.candle_count
            or source_candles != list(old_candles)
            or len(candles) <= len(old_candles)
            or candles[-len(old_candles):] != list(old_candles)
            or old_key[1:] != cache_key[1:]):
        return None
    old_identity, new_identity = old_key[0], cache_key[0]
    if (not isinstance(old_identity, tuple) or not isinstance(new_identity, tuple)
            or len(old_identity) != 9 or len(new_identity) != 9
            or any(old_identity[i] != new_identity[i] for i in (0, 3, 4))):
        return None
    history_window = indicator_history_target_bars_from_rules(rules)
    if (history_window <= 0 or len(old_candles) <= history_window + 16
            or len({descriptor.identity for descriptor in descriptors}) != len(descriptors)):
        return None
    selected = tuple(descriptor for descriptor in descriptors if descriptor.family in {
        FAMILY_RSI, FAMILY_MACD_SIGNAL, FAMILY_OCR_OSC,
    })
    if not selected:
        return None
    for descriptor in descriptors:
        parameters = descriptor.parameters
        condition = parameters.get("condition") or {}
        if ("AVG_PRICE" in descriptor.series_keys
                or (isinstance(condition, dict) and any(
                    str(condition.get(name) or "").upper() == "AVG_PRICE"
                    for name in ("target", "compare_target")
                ))):
            return None
        if descriptor in selected:
            try:
                if descriptor.family == FAMILY_RSI:
                    period = int(parameters.get("period") or 14)
                    sufficient = 0 < period + 1 <= history_window
                else:
                    fast = int(parameters.get("fast") or 12)
                    slow = int(parameters.get("slow") or 26)
                    signal = int(parameters.get("signal") or 9)
                    sufficient = (min(fast, slow, signal) > 0
                                  and max(fast, slow) + signal <= history_window)
            except (TypeError, ValueError):
                return None
            if not sufficient:
                return None
    try:
        if any(not math.isfinite(float(row["close"])) for row in candles):
            return None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    prepend = len(candles) - len(old_candles)
    prefix = build_validation_indicator_cache(
        candles[:prepend + history_window], rules, selected,
        cancel_predicate=cancel_event.is_set,
    )
    if cancel_event.is_set():
        return None
    old_selected = {
        (identity, channel): values for identity, channel, values in old_cache.series
        if identity in {descriptor.identity for descriptor in selected}
    }
    if (len(old_selected) != len(prefix.series)
            or any((identity, channel) not in old_selected
                   or len(old_selected[identity, channel]) != len(old_candles)
                   for identity, channel, _values in prefix.series)):
        return None
    stitched = {
        (identity, channel): values + old_selected[identity, channel][history_window:]
        for identity, channel, values in prefix.series
    }
    others = tuple(descriptor for descriptor in descriptors if descriptor not in selected)
    if others:
        full_others = build_validation_indicator_cache(
            candles, rules, others, cancel_predicate=cancel_event.is_set,
        )
        stitched.update({
            (identity, channel): values
            for identity, channel, values in full_others.series
        })
    if cancel_event.is_set():
        return None
    return ValidationIndicatorSeriesCache(
        len(candles),
        tuple((descriptor.identity, channel, stitched[descriptor.identity, channel])
              for descriptor in descriptors for channel in descriptor.series_keys
              if (descriptor.identity, channel) in stitched),
    )
_SIDECAR_UNSET = object()
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


def _trace_m3_sor_historical_error(
    *, stock: str, timeframe: str, response: object, reason: str,
    code_path: str, started: object = None, snapshot_present: bool = False,
    error_class: str = "",
) -> None:
    if stock != "000660" or timeframe != "M3":
        return
    try:
        observed = response if isinstance(response, dict) else {}
        request = started if isinstance(started, dict) else {}

        def safe_timestamp(value: object) -> str:
            if not isinstance(value, str) or len(value) > 40:
                return ""
            try:
                stamp = datetime.fromisoformat(value)
            except ValueError:
                return ""
            return stamp.isoformat() if stamp.tzinfo is not None else ""

        def safe_code(value: object) -> str:
            return value if isinstance(value, str) and re.fullmatch(
                r"[A-Z][A-Z0-9_]{0,79}", value,
            ) else "UNCLASSIFIED"

        safe_error_class = (
            error_class if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", error_class)
            else "UNCLASSIFIED"
        ) if error_class else ""

        identity = observed.get("market_data_identity")
        source = observed.get("market_source")
        trace_signal_validation_event(
            "v5_sor_historical_error",
            stock=stock, timeframe=timeframe,
            expected_market_source="INTEGRATED",
            expected_market_data_identity="000660_AL",
            observed_market_source=(
                source if isinstance(source, str) and source in {
                    "INTEGRATED", "KRX", "NXT", "SOR",
                } else "INVALID"
            ),
            observed_market_data_identity=(
                identity if isinstance(identity, str) and re.fullmatch(
                    r"[0-9]{6}(?:_AL)?", identity,
                ) else "INVALID"
            ),
            response_ok=observed.get("ok") is True,
            reason=safe_code(reason),
            error_class=safe_error_class,
            broker_route="request_minute_candles_display_only_integrated",
            source_snapshot_present=snapshot_present,
            request_as_of=safe_timestamp(request.get("request_as_of")),
            started_at=safe_timestamp(request.get("started_at")),
            received_at=safe_timestamp(observed.get("received_at")),
            code_path=code_path,
        )
    except Exception:
        return  # A diagnostic must not alter a source rejection.


def _v5_native_receipt_is_current(stock, started, response, broker, now, *, live_session=True) -> bool:
    """Bind native snapshots to their frozen source, request time and login."""
    try:
        if not isinstance(started, dict) or not isinstance(response, dict):
            return False
        requested = datetime.fromisoformat(started["request_as_of"])
        dispatched = datetime.fromisoformat(started["started_at"])
        received = datetime.fromisoformat(response["received_at"])
        if any(at.tzinfo is None for at in (requested, dispatched, received, now)):
            return False
        if not requested <= dispatched <= received <= now or received - requested >= timedelta(minutes=1):
            return False
        plan = read_stock_market_source_plan(PROJECT_ROOT, stock.code, now.astimezone(SEOUL_TIMEZONE).date())
        source = response.get("market_source")
        if source not in {"KRX", "NXT"}:
            return False
        identity = f"{stock.code}_NX" if source == "NXT" else stock.code
        if (plan is None or plan["stock_name"] != stock.name
                or plan["nxt_available"] is not (source == "NXT")):
            return False
        if live_session:
            selected_sessions = [source_for_time(plan, at) for at in (requested, received, now)]
            if (any(item is None or item["source"] != source
                    or item["market_data_identity"] != identity for item in selected_sessions)
                    or len({item["name"] for item in selected_sessions}) != 1):
                return False
        current = broker.broker_session_snapshot()
        epoch, login = started["connection_epoch"], started["login_session_id"]
        return (
            type(epoch) is int and epoch > 0 and isinstance(login, str) and bool(login)
            and current.connected is True
            and (current.connection_epoch, current.login_session_id) == (epoch, login)
            and all(item.get("market_data_identity") == identity
                    and item.get("market_source") == source and item.get("display_only") is True
                    and item.get("code") == stock.code for item in (started, response))
            and response.get("connection_epoch") == epoch
            and response.get("login_session_id") == login
            and response.get("request_as_of") == started["request_as_of"]
            and bool(started.get("rqname"))
            and response.get("request_id") == response.get("rqname") == started["rqname"]
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def _v5_native_forming_receipt_is_fresh(stock, receipt, forming, broker, now) -> bool:
    """A native partial survives minute rollover, but only briefly within its own bar."""
    try:
        started, response = receipt
        received = datetime.fromisoformat(response["received_at"])
        end = datetime.fromisoformat(forming["native_bar_end"])
        return (
            isinstance(forming, dict)
            and forming.get("is_complete") is False
            and forming == response.get("native_forming_candle")
            and received <= now < end
            and timedelta(0) <= now - received < timedelta(minutes=1)
            and _v5_native_receipt_is_current(stock, started, response, broker, now)
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def _v5_host_native_proof_is_current(stock, proof, broker, now) -> bool:
    """Check the host's copied broker request against the frozen live source."""
    try:
        started, response = proof["request_receipt"]
        requested = datetime.fromisoformat(started["request_as_of"])
        dispatched = datetime.fromisoformat(started["started_at"])
        received = datetime.fromisoformat(response["received_at"])
        source = proof["market_source"]
        interval = proof["timeframe_minutes"]
        identity = (f"{stock.code}_NX" if source == "NXT" else
                    stock.code if source == "KRX" else f"{stock.code}_AL")
        plan = read_stock_market_source_plan(PROJECT_ROOT, stock.code,
                                             now.astimezone(SEOUL_TIMEZONE).date())
        current = broker.broker_session_snapshot()
        selected = [source_for_time(plan, at) for at in (requested, received, now)]
        expected_source = "SOR" if source == "INTEGRATED" else source
        revision = proof.get("tick_revision", 0)
        if type(revision) is not int or revision < 0:
            return False
        if revision:
            tr = proof.get("tr_forming_candle")
            forming = proof.get("forming_candle")
            tick_received = datetime.fromisoformat(forming["tick_received_at"])
            expected_snapshot = {"NXT": "BROKER_NXT_TR_PLUS_CONTIGUOUS_TICKS",
                                 "INTEGRATED": "BROKER_AL_TR_PLUS_CONTIGUOUS_TICKS",
                                 "KRX": "BROKER_KRX_TR_PLUS_CONTIGUOUS_TICKS"}.get(source)
            if (expected_snapshot is None or type(revision) is not int or revision <= 0
                    or not isinstance(tr, dict) or not isinstance(forming, dict)
                    or forming.get("snapshot_source") != expected_snapshot
                    or forming.get("tick_receive_sequence") != revision
                    or tick_received < received or tick_received > now
                    or tr.get("time") != forming.get("time")
                    or tr.get("open") != forming.get("open")
                    or tr.get("volume", -1) > forming.get("volume", -1)
                    or tr.get("high", float("inf")) > forming.get("high", -1)
                    or tr.get("low", -1) < forming.get("low", float("inf"))):
                return False
        return (
            source in {"NXT", "INTEGRATED", "KRX"}
            and type(interval) is int and 0 < interval <= 240
            and plan is not None and plan["stock_name"] == stock.name
            and plan["nxt_available"] is (source != "KRX")
            and all(at.tzinfo is not None for at in (requested, dispatched, received, now))
            and requested <= dispatched <= received <= now
            and received - requested < timedelta(minutes=1)
            and all(item is not None and item["source"] == expected_source
                    and item["market_data_identity"] == identity
                    and item["name"] == selected[0]["name"] for item in selected)
            and current.connected is True
            and (current.connection_epoch, current.login_session_id)
            == proof["session_identity"]
            and proof["stock_code"] == stock.code
            and proof["market_data_identity"] == identity
            and proof["request_id"] == started["rqname"] == response["request_id"]
            == response["rqname"]
            and response["request_as_of"] == started["request_as_of"]
            and response["interval"] == interval
            and isinstance(response["rows"], list)
            and all(item["code"] == stock.code
                    and item["market_source"] == source
                    and item["market_data_identity"] == identity
                    and item["display_only"] is True
                    and (item["connection_epoch"], item["login_session_id"])
                    == proof["session_identity"] for item in (started, response))
        )
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return False


def _v5_host_native_forming_is_fresh(proof, now) -> bool:
    forming = proof.get("forming_candle")
    if not isinstance(forming, dict) or forming.get("is_complete") is not False:
        return False
    try:
        received = datetime.fromisoformat(
            forming.get("tick_received_at") or proof["request_receipt"][1]["received_at"])
        end = datetime.fromisoformat(forming["native_bar_end"])
        return received <= now < end and now - received < timedelta(minutes=1)
    except (KeyError, TypeError, ValueError):
        return False


def _v5_native_snapshot_parts(candles, plan, source, interval, requested, timeframe_key=None):
    """Freeze completion at request time; retain one native partial separately."""
    completed, forming = [], None
    for original in candles:
        start = candle_market_datetime(original)
        values = tuple(original.get(key) for key in ("open", "high", "low", "close", "volume"))
        if (start is None or start.second or start > requested
                or any(type(value) not in (int, float) or not math.isfinite(value) for value in values)
                or values[2] <= 0 or values[4] < 0
                or not values[2] <= values[0] <= values[1]
                or not values[2] <= values[3] <= values[1]):
            continue
        clock = start.strftime("%H:%M:%S")
        period = timeframe_key in {"D1", "W1", "MO1", "Y1"}
        selected = (source_for_time(plan, requested) if period else next(
            (item for item in plan["sessions"] if item["source"] == source
             and item["start_time"] <= clock < item["end_time"]), None))
        if selected is None:
            continue
        if period:
            if clock != "00:00:00" or selected["source"] != source:
                continue
            if timeframe_key == "D1":
                end = start + timedelta(days=1)
            elif timeframe_key == "W1":
                end = start + timedelta(days=7 - start.weekday())
            elif timeframe_key == "MO1":
                end = start.replace(year=start.year + int(start.month == 12),
                                    month=1 if start.month == 12 else start.month + 1, day=1)
            else:
                end = start.replace(year=start.year + 1, month=1, day=1)
        else:
            end = native_bar_end(plan, start, interval)
        if end is None:
            continue
        row = {key: original[key] for key in ("time", "open", "high", "low", "close", "volume")}
        if end <= requested or (source == "KRX" and clock == "15:30:00"):
            completed.append(row)
        elif period or start.date() == requested.date():
            forming = dict(row, is_complete=False, session=selected["name"],
                           market_source=source, market_data_identity=selected["market_data_identity"],
                           snapshot_request_as_of=requested.isoformat(), native_bar_end=end.isoformat())
    return completed, forming


class _V5SelectedSourceRequester:
    """Bind one selected timeframe to one broker request and response."""

    def __init__(self, broker, stock: ValidationStockRef, timeframe_key: str, integrated: bool,
                 *, on_verified_period_response=None, now_factory=None,
                 on_verified_native_response=None):
        if type(integrated) is not bool:
            raise ValueError("NXT_ELIGIBILITY_NOT_VERIFIED")
        if not integrated:
            plan = read_stock_market_source_plan(
                PROJECT_ROOT, stock.code, datetime.now(SEOUL_TIMEZONE).date(),
            )
            if (plan is None or plan["nxt_available"] is not False
                    or plan["stock_name"] != stock.name):
                raise ValueError("SOR_SOURCE_UNAVAILABLE")
        self.broker = broker
        self.stock = stock
        self.timeframe_key = timeframe_key
        self.integrated = integrated
        self.on_verified_period_response = on_verified_period_response
        self.now_factory = now_factory or (lambda: datetime.now(SEOUL_TIMEZONE))
        self.on_verified_native_response = on_verified_native_response
        self.market_source = "INTEGRATED" if integrated else "KRX"
        now = self.now_factory()
        if (integrated and now.astimezone(SEOUL_TIMEZONE).hour == 8
                and now.astimezone(SEOUL_TIMEZONE).minute < 50):
            plan = read_stock_market_source_plan(PROJECT_ROOT, stock.code, now.astimezone(SEOUL_TIMEZONE).date())
            selected = source_for_time(plan, now) if plan is not None else None
            if (selected is None or selected["source"] != "NXT"
                    or plan["stock_name"] != stock.name or plan["nxt_available"] is not True):
                raise ValueError("NXT_PRE_SOURCE_UNAVAILABLE")
            if timeframe_key not in _LIVE_MINUTE_TIMEFRAMES and timeframe_key not in {"D1", "W1", "MO1", "Y1"}:
                raise ValueError("NXT_NATIVE_TIMEFRAME_UNSUPPORTED")
            self.market_source = "NXT"

    def _request(self, method_name: str, code: str, name: str, count: int,
                 callback, *, interval: int | None = None) -> object:
        minute = self.timeframe_key in ("M1", "M3", "M5", "M10", "M15", "M30", "M60", "M120", "M240")
        if (code != self.stock.code or name != self.stock.name
                or minute != (interval is not None)
                or (minute and self.timeframe_key != f"M{interval}")):
            callback({"ok": False, "error": "V5_BROKER_REQUEST_IDENTITY_INVALID"})
            return None
        method = getattr(self.broker, method_name, None)
        if not callable(method):
            callback({"ok": False, "error": "V5_BROKER_REQUESTER_UNAVAILABLE"})
            return None
        capture_candidate = (self.market_source == "INTEGRATED" and not minute
                             and self.timeframe_key in {"W1", "MO1", "Y1"}
                             and callable(self.on_verified_period_response))
        request_started_at = self.now_factory() if capture_candidate else None
        session_reader = getattr(self.broker, "broker_session_snapshot", None)
        def read_session():
            if not capture_candidate or not callable(session_reader):
                return None
            try:
                return session_reader()
            except Exception:
                return None
        request_session = read_session()
        started = None
        request_exception_class = ""
        buffered = []
        completed = False
        expected_source = self.market_source
        expected_identity = (f"{code}_NX" if expected_source == "NXT" else
                             f"{code}_AL" if self.integrated else code)
        expected_trcode = (
            "opt10080" if minute else {
                "D1": "opt10081", "W1": "opt10082",
                "MO1": "opt10083", "Y1": "opt10094",
            }.get(self.timeframe_key)
        )

        def receive(response, *, received_at=None):
            nonlocal completed
            received_at = received_at or (self.now_factory() if capture_candidate else None)
            if started is None:
                buffered.append((response, received_at))
                return
            if completed:
                return
            completed = True
            if not isinstance(response, dict) or response.get("ok") is not True:
                raw_reason = response.get("reason_code") if isinstance(response, dict) else None
                _trace_m3_sor_historical_error(
                    stock=code, timeframe=self.timeframe_key, response=response,
                    reason=("BROKER_REQUEST_EXCEPTION" if request_exception_class
                            else raw_reason or "BROKER_RESPONSE_NOT_OK"),
                    error_class=(request_exception_class or
                                 ("INVALID_RESPONSE_TYPE" if not isinstance(response, dict)
                                  else "BROKER_ERROR_TEXT" if response.get("error")
                                  else "BROKER_RESPONSE_ERROR")),
                    code_path="broker_response", started=started,
                )
                callback(response if isinstance(response, dict) else
                         {"ok": False, "error": "V5_BROKER_RESPONSE_INVALID"})
                return
            request_id = str(started.get("rqname") or "") if isinstance(started, dict) else ""
            valid = (
                isinstance(started, dict) and started.get("ok") is True
                and bool(request_id)
                and ((self.integrated and expected_source != "NXT") or (
                    started.get("market_data_identity") == expected_identity
                    and started.get("market_source") == expected_source
                    and (not minute or started.get("display_only") is True)
                ))
                and response.get("request_id") == request_id
                and response.get("rqname") == request_id
                and response.get("code") == code
                and response.get("market_data_identity") == expected_identity
                and response.get("market_source") == expected_source
                and response.get("trcode") == expected_trcode
                and response.get("type") == ("minute_candles" if minute else "period_candles")
                and (response.get("interval") == interval if minute else
                     response.get("timeframe_key") == self.timeframe_key)
                and isinstance(response.get("rows"), list)
                and all(
                    not isinstance(row, dict) or (
                        row.get("market_data_identity", expected_identity) == expected_identity
                        and row.get("market_source", expected_source) == expected_source
                    ) for row in response.get("rows", ())
                )
            )
            native_receipt = (expected_source == "NXT" or
                              (minute and expected_source == "KRX" and
                               any(key in started or key in response for key in
                                   ("request_as_of", "received_at", "connection_epoch", "login_session_id"))))
            if valid and native_receipt:
                valid = _v5_native_receipt_is_current(
                    self.stock, started, response, self.broker, self.now_factory(),
                    live_session=expected_source == "NXT")
                if valid and callable(self.on_verified_native_response):
                    self.on_verified_native_response(started, response)
            if valid and capture_candidate:
                try:
                    self.on_verified_period_response(
                        code, self.timeframe_key, response,
                        started, request_started_at, received_at,
                        request_session,
                        read_session(),
                    )
                except (OSError, TypeError, ValueError):
                    pass  # Passive capture must not alter the selected-source result.
            if not valid:
                _trace_m3_sor_historical_error(
                    stock=code, timeframe=self.timeframe_key, response=response,
                    reason="BROKER_SOURCE_PROVENANCE_INVALID",
                    error_class="SOURCE_IDENTITY_MISMATCH",
                    code_path="broker_response", started=started,
                )
            callback(response if valid else
                     {"ok": False, "error": "V5_BROKER_SOURCE_PROVENANCE_INVALID"})

        kwargs = {"count": count, "callback": receive}
        if minute:
            kwargs["interval"] = interval
            if self.integrated:
                kwargs["nxt_available"] = True
        else:
            kwargs["timeframe_key"] = self.timeframe_key
            if self.integrated:
                kwargs["nxt_available"] = True
        try:
            started = method(code, name, **kwargs)
        except Exception as exc:
            request_exception_class = type(exc).__name__
            started = {"ok": False, "error": str(exc)}
        for response, received_at in buffered:
            receive(response, received_at=received_at)
        if not completed and (not isinstance(started, dict) or started.get("ok") is not True):
            receive(started)
        return started

    def request_minute_candles_read_only(self, code, name, *, interval, count, callback):
        return self._request("request_minute_candles_display_only_nxt" if self.market_source == "NXT"
                             else "request_minute_candles_display_only_integrated" if self.integrated
                             else "request_minute_candles_display_only_krx",
                             code, name, count, callback, interval=interval)

    def request_period_candles_read_only(self, code, name, *, timeframe_key, count, callback):
        if timeframe_key != self.timeframe_key:
            callback({"ok": False, "error": "V5_BROKER_REQUEST_IDENTITY_INVALID"})
            return None
        return self._request("request_period_candles_read_only_nxt" if self.market_source == "NXT"
                             else "request_period_candles_read_only_integrated" if self.integrated
                             else "request_period_candles_read_only",
                             code, name, count, callback)


def _read_v5_registration_statuses(
    records: tuple[dict[str, object], ...],
) -> dict[str, str]:
    """Read central registration and visible review states once per result set."""
    registered = {
        normalize_browser_code(stock.get("code")): stock
        for stock in read_base_stocks()
        if isinstance(stock, dict)
    }
    repo = StockRepository(PROJECT_ROOT)
    fallback_paths: dict[str, str] | None = None
    statuses: dict[str, str] = {}
    assignments: dict[str, str] = {}
    for record in records:
        code = normalize_browser_code(record.get("code"))
        stock = registered.get(code)
        if stock is None:
            statuses[code] = "\uB4F1\uB85D\uB300\uAE30"
            continue
        stock_path = str(stock.get("stock_path", "") or "").strip()
        if not stock_path:
            if fallback_paths is None:
                fallback_paths = {
                    item.code: item.stock_path for item in repo.list_stocks()
                }
            stock_path = fallback_paths.get(code, "")
        stock_dir = (
            repo.project_root / stock_path if stock_path
            else repo.stocks_dir / safe_stock_folder_name(
                code, str(stock.get("name") or record.get("name") or "")
            )
        )
        try:
            state = read_json_dict(stock_dir / "state.json")
            review = inspect_stock_review_state(stock_dir, loaded_state=state)
        except Exception:
            statuses[code] = "-"
            continue
        if review.review_required:
            statuses[code] = "\uAC80\uD1A0\uAD00\uB9AC"
            continue
        assigned_id = str(stock.get("assigned_routine_instance_id", "") or "").strip()
        if assigned_id:
            assignments[code] = assigned_id
        else:
            statuses[code] = "\uB4F1\uB85D\uB300\uAE30"
    if assignments:
        try:
            instance_names = {
                str(getattr(item, "instance_id", "") or "").strip():
                str(getattr(item, "display_name", "") or "").strip()
                for item in load_persisted_routine_instances()
            }
        except Exception:
            instance_names = None
        for code, assigned_id in assignments.items():
            statuses[code] = (
                "-" if instance_names is None
                else instance_names.get(assigned_id) or assigned_id
            )
    return statuses


class IndicatorFollowSignalValidationV5Flow(QObject):
    """Own V5 picker/window lifetime and fresh read-only replay requests."""

    validation_failed = pyqtSignal(str)
    signal_scan_completed = pyqtSignal(object)
    range_replay_completed = pyqtSignal(object)
    marking_completed = pyqtSignal(object)
    history_browse_completed = pyqtSignal(object)
    history_cache_completed = pyqtSignal(object)
    history_fetch_completed = pyqtSignal(object)
    filter_valid_evidence_completed = pyqtSignal(object)

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
        window_factory: Callable[..., object] = IndicatorFollowSignalValidationV5Window,
        recent_stock_store: object | None = None,
        preference_store: object | None = None,
        historical_cache: object | None = None,
        production_session_contract_reader: Callable[..., dict[str, object]] = (
            production_session_contract_for_stock
        ),
        full_parity_workers: int = 0,
        retain_broker_al_m1_evidence: bool = False,
        verified_sor_tail_provider: Callable[..., object] | None = None,
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
        if verified_sor_tail_provider is not None and not callable(verified_sor_tail_provider):
            raise TypeError("verified_sor_tail_provider must be callable")
        self._broker = broker
        self._validation_source_by_code: dict[str, bool] = {}
        self._validation_frozen_krx_codes: set[str] = set()
        self._validation_nxt_pre_codes: set[str] = set()
        self._native_minute_receipts: dict[str, tuple[dict, dict]] = {}
        self._native_minute_completed_content: dict[str, str] = {}
        self._native_selected_pending: dict[int, tuple[object, dict, int]] = {}
        self._native_selected_last: dict[int, tuple[str, int]] = {}
        self._native_source_transition: dict[int, dict] = {}
        self._native_transition_nx_pools: dict[int, dict] = {}
        self._native_transition_nx_bindings: dict[int, tuple[object, object, int]] = {}
        self._operation_active_reader = operation_active_reader
        self._historical_count = historical_count
        self._historical_provider_factory = historical_provider_factory
        self._retain_broker_al_m1_evidence = retain_broker_al_m1_evidence
        self._broker_al_m1_request_receipts: dict[int, list[tuple[dict, dict]]] = {}
        self._replay_factory = replay_factory
        self._verified_sor_tail_provider = verified_sor_tail_provider
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
            stock_picker_factory=lambda picker_parent: IndicatorFollowValidationStockPicker(
                picker_parent, market_snapshot_api=broker,
                registration_status_reader=_read_v5_registration_statuses,
            ),
        )
        restored = self._read_recent_stocks()
        self._last_selected_stock = restored[0] if restored else None
        self._bound_dialog_ids: set[int] = set()
        self._open_windows: dict[int, object] = {}
        self._source_initialization_failed: set[int] = set()
        self._request_generation: dict[int, int] = {}
        self._prepare_active: dict[int, object] = {}
        self._prepare_pending: dict[int, dict[str, object]] = {}
        self._prepare_deferred: dict[int, tuple[int, dict[str, object]]] = {}
        self._range_replay_generation: dict[int, int] = {}
        self._range_replay_active: dict[int, object] = {}
        self._range_replay_pending: dict[int, dict[str, object]] = {}
        self._range_replay_deferred: dict[int, tuple[int, dict[str, object]]] = {}
        self._filter_valid_evidence_generation: dict[int, int] = {}
        self._filter_valid_evidence_active: dict[int, object] = {}
        self._filter_valid_evidence_pending: dict[int, dict[str, object]] = {}
        self._active_run_identity: dict[int, tuple[object, ...]] = {}
        self._history_browse_active: dict[int, object] = {}
        # One accepted, source-bound full indicator cache per live V5 window.
        self._history_browse_indicator_cache: dict[int, tuple[object, ...]] = {}
        self._sor_m3_marking_published: dict[int, tuple[object, ...]] = {}
        self._sor_m3_replay_published: dict[int, tuple[object, ...]] = {}
        self._history_browse_deferred: dict[int, tuple[int, dict[str, object]]] = {}
        self._market_snapshot_generation: dict[int, int] = {}
        self._market_snapshot_metadata: dict[int, tuple[str, dict[str, object]]] = {}
        self._active_providers: dict[tuple[int, int], object] = {}
        self._historical_pools: dict[int, dict[str, object]] = {}
        self._validation_sessions: dict[int, ValidationSession] = {}
        self._history_targets: dict[int, int] = {}
        self._history_extension_inflight: dict[int, dict[str, object]] = {}
        self._history_extension_pending: dict[int, tuple[int, object]] = {}
        self._history_exhaustion: dict[int, tuple[object, ...]] = {}
        self._integrated_display_sidecars: dict[
            tuple[str, str, int], dict[str, object]
        ] = {}
        self._integrated_display_inflight: dict[
            tuple[str, str, int], int
        ] = {}
        self._marking_source_revisions: dict[int, dict[str, object]] = {}
        self._last_run_requests: dict[int, IndicatorFollowSignalValidationRunRequest] = {}
        self._sor_m1_observed_host = None
        self._sor_m1_last_snapshot: dict[int, tuple[object, ...]] = {}
        self._v5_lease_hosts: dict[int, object] = {}
        self._offsession_sor_m1_requests: dict[int, dict[str, object]] = {}
        self._sor_m1_diagnostic_skips: dict[int, tuple[str, str]] = {}
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
        self.marking_completed.connect(self._on_marking_completed)
        self.history_browse_completed.connect(self._on_history_browse_completed)
        self.history_cache_completed.connect(self._on_history_cache_completed)
        self.history_fetch_completed.connect(self._on_history_fetch_completed)
        self.filter_valid_evidence_completed.connect(
            self._on_filter_valid_evidence_completed
        )
        self._host.validation_blocked.connect(self._forward_host_failure)

    @property
    def host(self) -> IndicatorFollowValidationHost:
        return self._host

    def _market_source_observation(self, stock_code: str) -> dict[str, object]:
        """Read source status for diagnostics; never request or register data."""

        observation = {
            "host_available": False,
            "krx_available": False,
            "krx_quality": None,
            "nxt_available": False,
            "nxt_quality": None,
        }
        try:
            owner = self.parent()
            getter = getattr(owner, "existing_market_data_host", None)
            market = getter() if callable(getter) else None
            if market is None:
                return observation
            observation["host_available"] = True
            krx = market.high_resolution_market_state(stock_code)
            nxt = market.nxt_display_live_price_state(stock_code)
            observation.update(
                krx_available=krx is not None,
                krx_quality=getattr(krx, "data_quality", None),
                nxt_available=nxt is not None,
                nxt_quality=getattr(nxt, "data_quality", None),
            )
        except Exception as exc:
            observation["observation_error"] = type(exc).__name__
        return observation

    def _sor_registration_observation(self, stock_code: str) -> dict[str, object]:
        """Project existing KRX/NXT registrations and counters into the opt-in trace."""

        try:
            owner = self.parent()
            getter = getattr(owner, "existing_market_data_host", None)
            market = getter() if callable(getter) else None
            if market is None:
                return {"host_available": False}
            api = getattr(market, "kiwoom_api", None)
            krx_getter = getattr(api, "realtime_shadow_registration_snapshot", None)
            nxt_getter = getattr(api, "nxt_display_registration_snapshot", None)
            krx = krx_getter() if callable(krx_getter) else None
            nxt = nxt_getter() if callable(nxt_getter) else None

            def registration(snapshot, *fields):
                if snapshot is None:
                    return None
                session = str(getattr(snapshot, "login_session_id", "") or "")
                result = {
                    "active": bool(getattr(snapshot, "active", False)),
                    "connection_epoch": getattr(snapshot, "connection_epoch", None),
                    "session_sha256": hashlib.sha256(session.encode("utf-8")).hexdigest()
                    if session else "",
                    "fid_list": tuple(getattr(snapshot, "fid_list", ()) or ()),
                    "screen_batches": tuple(
                        (str(getattr(batch, "screen_no", "")),
                         tuple(getattr(batch, "stock_codes", ()) or ()))
                        for batch in (getattr(snapshot, "screen_batches", ()) or ())
                    ),
                }
                result.update(
                    (field, tuple(getattr(snapshot, field, ()) or ()))
                    for field in fields
                )
                return result

            counters = market.high_resolution_market_data_snapshot()
            krx_state = market.high_resolution_market_state(stock_code)
            nxt_state = market.nxt_display_live_price_state(stock_code)
            return {
                "host_available": True,
                "krx_registration": registration(
                    krx, "target_stock_codes", "shadow_target_stock_codes"
                ),
                "nxt_registration": registration(
                    nxt, "canonical_stock_codes", "broker_code_identities"
                ),
                "krx_ticks": {
                    "received": counters.received_tick_count,
                    "processed": counters.processed_tick_count,
                    "overflow": counters.overflow_count,
                    "queue_depth": counters.current_queue_depth,
                    "stock_received": getattr(krx_state, "received_tick_count", None),
                    "stock_processed": getattr(krx_state, "processed_tick_count", None),
                },
                "nxt_ticks": {
                    "api_receive_sequence": getattr(
                        api, "_nxt_display_receive_sequence", None
                    ),
                    "stock_last_receive_sequence": getattr(
                        nxt_state, "receive_sequence", None
                    ),
                    "queue_depth": len(getattr(market, "_nxt_display_tick_queue", ())),
                },
            }
        except Exception as exc:
            return {"host_available": True, "observation_error": type(exc).__name__}

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
        signal = getattr(dialog, "signal_validation_v5_requested", None)
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
            marking_signal = getattr(window, "marking_evaluation_requested", None)
            if callable(getattr(marking_signal, "connect", None)):
                marking_signal.connect(lambda request, ref=window_ref: self._start_marking(ref(), request))
            run_signal.connect(
                lambda snapshot, ref=window_ref: self._run_validation(ref(), snapshot)
            )
            valid_evidence_signal = getattr(
                window,
                "filter_valid_evidence_requested",
                None,
            )
            if callable(getattr(valid_evidence_signal, "connect", None)):
                valid_evidence_signal.connect(
                    lambda request, ref=window_ref: (
                        self._run_filter_valid_evidence(ref(), request)
                    )
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
            self._bind_sor_m1_observations()
            destroyed = getattr(window, "destroyed", None)
            if callable(getattr(destroyed, "connect", None)):
                def release_destroyed_window(_obj=None, window_key=key, flow_ref=weakref.ref(self)):
                    # Qt may destroy the window while its owning flow is being collected.
                    flow = flow_ref()
                    if flow is not None:
                        flow._release_window(window_key)

                destroyed.connect(release_destroyed_window)
            window.show()
            if (self._last_selected_stock is not None
                    and not self._initialize_selected_stock_source(window, self._last_selected_stock)):
                return window
            self._sync_v5_observation_lease(window)
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

    def _bind_sor_m1_observations(self) -> None:
        try:
            owner = self.parent()
            getter = getattr(owner, "existing_market_data_host", None)
            market = getter() if callable(getter) else None
            signal = getattr(market, "market_data_observed", None)
            connect = getattr(signal, "connect", None)
            if market is not None and market is not self._sor_m1_observed_host and callable(connect):
                connect(self._on_sor_m1_snapshot_accepted)
                self._sor_m1_observed_host = market
        except (AttributeError, RuntimeError, TypeError):
            return

    def _note_sor_m1_skip(self, key: int, code: str, reason: str) -> None:
        marker = (code, reason)
        if self._sor_m1_diagnostic_skips.get(key) != marker:
            self._sor_m1_diagnostic_skips[key] = marker
            _trace_sor_al_m1("request_skipped", code, reason=reason)

    def _sync_v5_observation_lease(self, window: object) -> None:
        key = id(window)
        if self._open_windows.get(key) is not window:
            return
        owner = self.parent()
        getter = getattr(owner, "existing_market_data_host", None)
        try:
            market = getter() if callable(getter) else None
        except (AttributeError, RuntimeError, TypeError):
            self._note_sor_m1_skip(key, getattr(getattr(window, "stock", None), "code", ""),
                                   "MARKET_HOST_ERROR")
            return
        setter = getattr(market, "set_v5_observation_lease", None)
        if not callable(setter):
            self._note_sor_m1_skip(key, getattr(getattr(window, "stock", None), "code", ""),
                                   "LEASE_API_UNAVAILABLE")
            return
        previous = self._v5_lease_hosts.get(key)
        if previous is not None and previous is not market:
            release = getattr(previous, "set_v5_observation_lease", None)
            if callable(release):
                release(key, None)
            self._v5_lease_hosts.pop(key, None)
        stock = getattr(window, "stock", None)
        code = stock.code if isinstance(stock, ValidationStockRef) else None
        try:
            run_request = self._last_run_requests.get(key)
            timeframe = (validation_timeframe_from_rules(
                run_request.settings_snapshot.to_dict()).get("minutes")
                if isinstance(run_request, IndicatorFollowSignalValidationRunRequest)
                else None)
            result = setter(key, code, timeframe_minutes=timeframe or 0) if code else setter(key, None)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            if code:
                self._v5_lease_hosts[key] = market
                self._note_sor_m1_skip(key, code, "LEASE_ERROR")
            return
        if code:
            self._v5_lease_hosts[key] = market
        if isinstance(result, dict) and result.get("ok") is True and code:
            self._bind_sor_m1_observations()
        elif code:
            self._note_sor_m1_skip(key, code, "LEASE_REJECTED")
        else:
            self._note_sor_m1_skip(key, "", "NO_STOCK")

    def _request_offsession_sor_m1_once(
        self, window: object, market: object, code: str,
    ) -> None:
        """Try the governed AL M1 request at most twice outside live sessions."""
        from gui_market_data_host import MarketDataHost

        key = id(window)
        if (self._open_windows.get(key) is not window
                or self._validation_source_by_code.get(code) is not True):
            self._note_sor_m1_skip(key, code, "SOURCE_UNVERIFIED")
            return
        try:
            now = self._now_factory()
            now = (
                now.replace(tzinfo=SEOUL_TIMEZONE)
                if now.tzinfo is None else now.astimezone(SEOUL_TIMEZONE)
            )
            active = MarketDataHost._display_session_for_time(now) in {
                "NXT_PRE", "KRX", "NXT_AFTER",
            }
            if now.weekday() < 5 and active:
                self._note_sor_m1_skip(key, code, "ACTIVE_SESSION")
                return
            identity = MarketDataHost._current_display_session_identity(market)
            if code not in MarketDataHost._nxt_eligible_targets((code,)):
                self._note_sor_m1_skip(key, code, "NXT_INELIGIBLE")
                return
            required = getattr(market, "_sor_m1_required_by_stock", None)
            required_count = (
                int(required.get(code, 0)) if isinstance(required, dict) else 0
            )
            count = min(
                DEFAULT_CANDLES_MAX_COUNT,
                max(MarketDataHost._full_day_display_count(1), required_count),
            )
            prior = self._offsession_sor_m1_requests.get(key)
            if (isinstance(prior, dict) and prior.get("market") is market
                    and prior.get("code") == code and prior.get("identity") == identity):
                if prior.get("snapshot_persisted") is True and prior.get("count") == count:
                    return
                if prior.get("completed") is True:
                    return
                if prior.get("pending") or prior.get("scheduled"):
                    self._note_sor_m1_skip(key, code, "REQUEST_IN_FLIGHT")
                    return
            session_m1 = MarketDataHost._current_display_candles(market, code, 1)
            if (isinstance(prior, dict) and prior.get("market") is market
                    and prior.get("code") == code and prior.get("identity") == identity
                    and prior.get("requests", 0) >= 2 and len(session_m1) < count):
                self._note_sor_m1_skip(key, code, "ATTEMPTS_EXHAUSTED")
                return
            if session_m1 and self._persist_offsession_session_m1(
                window.stock, market, identity, session_m1, count, now,
            ):
                self._offsession_sor_m1_requests[key] = {
                    "snapshot_persisted": True, "market": market, "code": code,
                    "identity": identity, "count": count,
                }
                self._note_sor_m1_skip(key, code, "SESSION_M1_PERSISTED")
                return
        except (AttributeError, RuntimeError, TypeError, ValueError):
            self._note_sor_m1_skip(key, code, "PREREQUISITE_ERROR")
            return

        state = self._offsession_sor_m1_requests.get(key)
        if (not isinstance(state, dict)
                or state.get("code") != code or state.get("market") is not market
                or (state.get("identity") is not None and identity is not None
                    and state["identity"] != identity)):
            state = {"code": code, "market": market, "identity": identity,
                     "requests": 0, "polls": 0, "pending": False, "scheduled": False}
            self._offsession_sor_m1_requests[key] = state
        if state["pending"] or state["scheduled"]:
            self._note_sor_m1_skip(key, code, "REQUEST_IN_FLIGHT")
            return

        def schedule() -> None:
            if state["scheduled"]:
                return
            state["scheduled"] = True

            def retry() -> None:
                if self._offsession_sor_m1_requests.get(key) is not state:
                    return
                state["scheduled"] = False
                if (self._open_windows.get(key) is window
                        and getattr(getattr(window, "stock", None), "code", None) == code):
                    self._request_offsession_sor_m1_once(window, market, code)

            QTimer.singleShot(1000, retry)

        if identity is None:
            self._note_sor_m1_skip(key, code, "SESSION_UNAVAILABLE")
            if state["polls"] < 3:
                state["polls"] += 1
                schedule()
            elif state.get("reason") is None:
                state["reason"] = "SESSION_UNAVAILABLE"
                self.validation_failed.emit("SOR_M1_ACQUISITION_FAILED:SESSION_UNAVAILABLE")
            return
        if state["identity"] is None:
            state["identity"] = identity
        if state["requests"] >= 2:
            self._note_sor_m1_skip(key, code, "ATTEMPTS_EXHAUSTED")
            return
        self._sor_m1_diagnostic_skips.pop(key, None)
        _trace_sor_al_m1("request_ready", code, count=count,
                         required_count=required_count, connection_epoch=identity[0],
                         attempt=state["requests"] + 1)
        state["requests"] += 1
        state["pending"] = True
        attempt = object()
        state["attempt"] = attempt
        cache_lease = _CACHE_LEASE_UNSET
        capture_lease = getattr(self._historical_cache, "capture_store_lease", None)
        if callable(capture_lease):
            try:
                cache_lease = capture_lease(code)
            except Exception:
                pass

        def terminal(result: object) -> None:
            current_identity = MarketDataHost._current_display_session_identity(market)
            if (self._offsession_sor_m1_requests.get(key) is not state
                    or state.get("attempt") is not attempt
                    or not state["pending"]
                    or self._open_windows.get(key) is not window
                    or getattr(getattr(window, "stock", None), "code", None) != code
                    or current_identity != identity):
                _trace_sor_al_m1("callback_ignored", code, reason="CONTEXT_CHANGED",
                                 attempt=state["requests"],
                                 epoch_match=current_identity == identity)
                return
            state["pending"] = False
            install_result = result.get("install_result") if isinstance(result, dict) else None
            installed_candles = MarketDataHost._current_display_candles(market, code, 1)
            last_installed = (candle_market_datetime(installed_candles[-1])
                              if installed_candles else None)
            installed = (isinstance(result, dict) and result.get("ok") is True
                         and isinstance(install_result, dict)
                         and install_result.get("ok") is True
                          and bool(installed_candles))
            source_match = (isinstance(result, dict)
                            and result.get("code") == code
                            and result.get("interval") == 1
                            and result.get("market_data_identity") == f"{code}_AL"
                            and result.get("market_source") == "INTEGRATED"
                            and result.get("display_only") is True)
            epoch_match = (isinstance(result, dict)
                           and (result.get("connection_epoch"),
                                result.get("login_session_id")) == identity)
            rows = result.get("rows") if isinstance(result, dict) else None
            _trace_sor_al_m1("callback_terminal", code, attempt=state["requests"],
                             response_ok=isinstance(result, dict) and result.get("ok") is True,
                             install_ok=isinstance(install_result, dict)
                             and install_result.get("ok") is True,
                             installed=installed, source_match=source_match,
                             epoch_match=epoch_match, received_count=len(rows)
                             if isinstance(rows, list) else 0,
                             installed_count=len(installed_candles),
                             last_minute=last_installed.isoformat(timespec="minutes")
                             if last_installed else "",
                             reason=(result.get("reason_code") or
                                     (install_result.get("reason_code")
                                      if isinstance(install_result, dict) else None)
                                     or ("OK" if result.get("ok") is True
                                         else "BROKER_RESPONSE_FAILED"))
                             if isinstance(result, dict) else "INVALID_RESULT")
            if not installed:
                state["reason"] = (str(result.get("reason_code") or result.get("error")
                                       or (install_result.get("reason_code")
                                           if isinstance(install_result, dict) else None)
                                       or "M1_NOT_INSTALLED")
                                   if isinstance(result, dict) else "M1_REQUEST_FAILED")
                if state["requests"] < 2:
                    schedule()
                else:
                    self.validation_failed.emit(f"SOR_M1_ACQUISITION_FAILED:{state['reason']}")
                return
            self._persist_offsession_sor_m1(
                window.stock, market, identity, result, count, now, cache_lease,
            )
            if source_match and epoch_match:
                state["completed"] = True
            if not getattr(window, "_normal_base_requested", False):
                return

            def retry() -> None:
                if (self._offsession_sor_m1_requests.get(key) is state
                        and self._open_windows.get(key) is window
                        and getattr(getattr(window, "stock", None), "code", None) == code):
                    self._request_initial_validation(window)

            QTimer.singleShot(0, retry)

        try:
            from auto_candle_refresh import request_display_timeframe_candles_for_stock

            started = request_display_timeframe_candles_for_stock(
                window=market,
                code=code,
                name=getattr(getattr(window, "stock", None), "name", ""),
                operation_cycle_minute_key=now.strftime("%Y-%m-%d %H:%M"),
                timeframe_minutes=1,
                count=count,
                nxt_available=True,
                on_terminal=terminal,
            )
            if isinstance(started, dict) and started.get("ok") is True:
                _trace_sor_al_m1("request_dispatched", code, attempt=state["requests"],
                                 count=count, response_ok=True)
            if (not isinstance(started, dict) or started.get("ok") is not True) and state["pending"]:
                _trace_sor_al_m1("immediate_rejection", code, attempt=state["requests"],
                                 reason=(started.get("reason_code") or "REQUEST_REJECTED")
                                 if isinstance(started, dict) else "INVALID_START_RESULT")
                terminal(started if isinstance(started, dict) else
                         {"ok": False, "reason_code": "INVALID_START_RESULT"})
            elif isinstance(started, dict) and started.get("ok") is True and state["pending"]:
                def callback_timeout() -> None:
                    if (self._offsession_sor_m1_requests.get(key) is not state
                            or state.get("attempt") is not attempt
                            or not state["pending"]
                            or self._open_windows.get(key) is not window
                            or getattr(getattr(window, "stock", None), "code", None) != code
                            or MarketDataHost._current_display_session_identity(market) != identity):
                        return
                    state["pending"] = False
                    timed_out_attempt = state["requests"]
                    state["requests"] = 2
                    state["reason"] = "CALLBACK_TIMEOUT"
                    _trace_sor_al_m1("callback_timeout", code, attempt=timed_out_attempt,
                                     count=count, connection_epoch=identity[0])
                    self.validation_failed.emit("SOR_M1_ACQUISITION_FAILED:CALLBACK_TIMEOUT")

                QTimer.singleShot(30000, callback_timeout)
        except Exception as exc:
            _trace_sor_al_m1("immediate_rejection", code, attempt=state["requests"],
                             reason=f"REQUEST_{type(exc).__name__.upper()}")
            terminal({"ok": False, "reason_code": f"REQUEST_{type(exc).__name__}"})

    def _persist_offsession_session_m1(
        self, stock, market, identity, session_m1, count, as_of,
    ) -> bool:
        """Store a complete, current-session direct AL snapshot under a cache lease."""
        from gui_market_data_host import MarketDataHost

        reason = "SESSION_M1_INVALID"
        try:
            if identity is None:
                return False
            candles = []
            last_time = None
            for row in session_m1:
                bar_time = candle_market_datetime(row)
                values = tuple(row.get(field) for field in
                               ("open", "high", "low", "close", "volume"))
                if (not isinstance(row, dict)
                        or row.get("market_data_identity") != f"{stock.code}_AL"
                        or row.get("market_source") != "INTEGRATED"
                        or row.get("display_only") is not True
                        or row.get("is_complete") is not True
                        or row.get("timeframe_minutes") != 1
                        or bar_time is None or bar_time.utcoffset() != timedelta(hours=9)
                        or bar_time.second or bar_time.microsecond
                        or bar_time.weekday() >= 5
                        or bar_time + timedelta(minutes=1) > as_of
                        or (last_time is not None and bar_time <= last_time)
                        or row.get("session") != MarketDataHost._display_session_for_time(bar_time)
                        or not row.get("session")
                        or row.get("trade_date") != bar_time.date().isoformat()
                        or any(type(value) not in (int, float) or not math.isfinite(value)
                               for value in values)
                        or values[2] <= 0 or values[4] < 0
                        or not values[2] <= values[0] <= values[1]
                        or not values[2] <= values[3] <= values[1]):
                    return False
                last_time = bar_time
                candles.append({
                    "time": bar_time.strftime("%Y%m%d%H%M%S"),
                    **{field: row[field] for field in
                       ("open", "high", "low", "close", "volume")},
                })
            if len(candles) < count:
                reason = "SESSION_M1_INSUFFICIENT"
                return False
            capture_lease = getattr(self._historical_cache, "capture_store_lease", None)
            if (not callable(capture_lease)
                    or not callable(getattr(self._historical_cache,
                                            "store_if_lease_current", None))):
                reason = "CACHE_LEASE_UNAVAILABLE"
                return False
            cache_lease = capture_lease(stock.code)
            previous = self._cached_history(stock, 1, "M1", count)
            if previous is not None and previous.candles[-1]["time"] > candles[-1]["time"]:
                reason = "CACHE_NEWER"
                return False
            capacity = max(count, previous.requested_count) if previous else count
            merged = merge_validation_candles(
                previous.candles if previous else (), candles, capacity,
            )
            if (MarketDataHost._current_display_session_identity(market) != identity
                    or MarketDataHost._current_display_candles(market, stock.code, 1)
                    != session_m1):
                reason = "SESSION_CHANGED"
                return False
            persisted = self._persist_history(
                stock, 1, "M1", capacity, merged,
                market_data_identity=f"{stock.code}_AL", market_source="INTEGRATED",
                cache_lease=cache_lease,
            )
            reason = "OK" if persisted else "STORE_REJECTED"
            return persisted
        except Exception:
            reason = "SESSION_M1_INVALID"
            return False
        finally:
            _trace_sor_al_m1("persist_result", stock.code, reason=reason,
                             persisted=reason == "OK", count=len(session_m1))

    def _persist_offsession_sor_m1(
        self, stock, market, identity, result, count, as_of, cache_lease,
    ) -> None:
        """Persist only completed rows from the accepted direct AL M1 terminal."""
        def diagnostic(reason: str, *, persisted: bool = False,
                       candles: object = None) -> None:
            rows = candles if isinstance(candles, list) else []
            last = candle_market_datetime(rows[-1]) if rows else None
            _trace_sor_al_m1("persist_result", stock.code, reason=reason,
                             persisted=persisted, count=len(rows),
                             last_minute=last.isoformat(timespec="minutes") if last else "",
                             cache_lease_present=cache_lease is not _CACHE_LEASE_UNSET)

        if (cache_lease is _CACHE_LEASE_UNSET
                or not callable(getattr(self._historical_cache, "store_if_lease_current", None))
                or not isinstance(result, dict)
                or result.get("code") != stock.code
                or result.get("interval") != 1
                or result.get("market_data_identity") != f"{stock.code}_AL"
                or result.get("market_source") != "INTEGRATED"
                or result.get("display_only") is not True
                or (result.get("connection_epoch"), result.get("login_session_id")) != identity
                or not isinstance(result.get("rows"), list)):
            diagnostic("EVIDENCE_INVALID")
            return
        try:
            from gui_market_data_host import MarketDataHost

            installed = MarketDataHost._current_display_candles(market, stock.code, 1)
            if not installed or any(
                row.get("is_complete") is not True
                or row.get("display_only") is not True
                or row.get("market_data_identity") != f"{stock.code}_AL"
                or row.get("market_source") != "INTEGRATED"
                for row in installed
            ):
                diagnostic("INSTALLED_M1_INVALID")
                return
            snapshot = ValidationHistoricalSnapshot(
                stock=stock, timeframe_minutes=1, timeframe_key="M1",
                requested_count=count, request_id="SOR_AL_M1_DISPLAY_TERMINAL",
                rows=result["rows"], market_data_identity=f"{stock.code}_AL",
                market_source="INTEGRATED", request_as_of=as_of.isoformat(),
            )
            projected, _ = project_validation_candles(snapshot)
            completed = [
                row for row in projected
                if (bar_time := candle_market_datetime(row)) is not None
                and MarketDataHost._display_session_for_time(bar_time)
                and bar_time + timedelta(minutes=1) <= as_of
            ]
            latest = installed[-1]
            if (not completed
                    or candle_market_datetime(completed[-1]) != candle_market_datetime(latest)
                    or any(completed[-1].get(field) != latest.get(field)
                           for field in ("open", "high", "low", "close", "volume"))):
                diagnostic("LAST_MINUTE_MISMATCH", candles=completed)
                return
            previous = self._cached_history(stock, 1, "M1", count)
            if previous is not None and previous.candles[-1]["time"] > completed[-1]["time"]:
                diagnostic("CACHE_NEWER", candles=completed)
                return
            capacity = max(count, previous.requested_count) if previous else count
            merged = merge_validation_candles(
                previous.candles if previous else (), completed, capacity,
            )
            persisted = self._persist_history(
                stock, 1, "M1", capacity, merged,
                market_data_identity=f"{stock.code}_AL", market_source="INTEGRATED",
                cache_lease=cache_lease,
            )
            diagnostic("OK" if persisted else "STORE_REJECTED",
                       persisted=persisted, candles=completed)
        except Exception as exc:
            diagnostic(f"EXCEPTION_{type(exc).__name__.upper()}")
            return

    def _release_v5_observation_lease(self, key: int) -> None:
        self._native_selected_pending.pop(key, None)
        self._native_selected_last.pop(key, None)
        self._native_source_transition.pop(key, None)
        self._native_transition_nx_pools.pop(key, None)
        self._native_transition_nx_bindings.pop(key, None)
        self._offsession_sor_m1_requests.pop(key, None)
        self._sor_m1_diagnostic_skips.pop(key, None)
        market = self._v5_lease_hosts.pop(key, None)
        setter = getattr(market, "set_v5_observation_lease", None)
        if callable(setter):
            try:
                setter(key, None)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass

    def _on_sor_m1_snapshot_accepted(self, event: object) -> None:
        try:
            self._handle_sor_m1_snapshot_accepted(event)
        except (AttributeError, KeyError, RuntimeError, TypeError, ValueError):
            return

    def _handle_sor_m1_snapshot_accepted(self, event: object) -> None:
        if isinstance(event, dict) and event.get("source") in {
                "NATIVE_SELECTED_FORMING_ACCEPTED", "NATIVE_SELECTED_COMPLETED_ACCEPTED",
                "NATIVE_SELECTED_TICK_ACCEPTED"}:
            self._handle_native_selected_snapshot_accepted(event)
            return
        if (not isinstance(event, dict) or event.get("source") not in
                {"SOR_AL_M1_SNAPSHOT_ACCEPTED", "SOR_AL_NATIVE_M1_PARITY_ACCEPTED"}):
            return
        owner = self.parent()
        getter = getattr(owner, "existing_market_data_host", None)
        market = getter() if callable(getter) else None
        code = event.get("stock_code")
        if not isinstance(code, str):
            return
        identity = (event.get("connection_epoch"), event.get("login_session_id"))
        snapshot_key = (*identity, event.get("forming_minute"),
                        event.get("snapshot_received_at"))
        current_session = getattr(market, "_current_display_session_identity", None)
        forming_by_stock = getattr(market, "_sor_forming_m1_by_stock", None)
        required_by_stock = getattr(market, "_sor_m1_required_by_stock", None)
        forming = forming_by_stock.get(code) if isinstance(forming_by_stock, dict) else None
        candle = forming.get("candle") if isinstance(forming, dict) else None
        if (
            market is not self._sor_m1_observed_host
            or event.get("market_data_identity") != f"{code}_AL"
            or self._validation_source_by_code.get(code) is not True
            or not isinstance(required_by_stock, dict) or code not in required_by_stock
            or not callable(current_session)
            or current_session() != identity
            or not all(isinstance(value, str) and value for value in snapshot_key[2:])
            or (not isinstance(forming, dict)
                or forming.get("session_identity") != identity
                or not isinstance(candle, dict)
                or candle.get("snapshot_source") not in _V5_SOR_FORMING_SOURCES
                or snapshot_key[2:] != (candle.get("bar_time"),
                                        candle.get("snapshot_received_at")))
        ):
            return
        for key, window in tuple(self._open_windows.items()):
            session = self._validation_sessions.get(key)
            pool = self._historical_pools.get(key)
            marking = getattr(window, "marking_display_check", None)
            marked = getattr(marking, "isChecked", None)
            busy = getattr(window, "is_chart_interaction_busy", None)
            if (
                not isinstance(session, ValidationSession)
                or not isinstance(pool, dict)
                or session.request.stock.code != code
                or session.request.timeframe_key != "M1"
                or pool.get("stock") != session.request.stock
                or pool.get("market_source") != "INTEGRATED"
                or pool.get("market_data_identity") != f"{code}_AL"
                or pool.get("timeframe_key") != session.request.timeframe_key
                or pool.get("timeframe_minutes") != session.request.timeframe_minutes
                or not getattr(window, "_normal_base_ready", False)
                or not getattr(window, "_normal_base_requested", False)
                or getattr(window, "_replay_snapshot", None) is not None
                or getattr(window, "validation_range", None) is not None
                or not callable(marked) or marked()
                or not callable(busy) or busy()
                or not self._window_accepts_results(window)
                or key in self._range_replay_active
                or key in self._history_browse_active
                or getattr(window, "_marking_pool_publication_pending", False)
                or self._sor_m1_last_snapshot.get(key) == snapshot_key
            ):
                continue
            try:
                view, deficit = self._normal_base_history_view(
                    session, pool, self._history_targets.get(key, self._historical_count),
                )
                if deficit:
                    continue
                self._use_pool_for_window(
                    window, session, pool, evaluation_count=100,
                    _normal_view=view,
                    live_m1_snapshot_key=snapshot_key,
                )
            except ValueError:
                continue
            self._sor_m1_last_snapshot[key] = snapshot_key

    def _handle_native_selected_snapshot_accepted(self, event: dict) -> None:
        owner = self.parent()
        getter = getattr(owner, "existing_market_data_host", None)
        market = getter() if callable(getter) else None
        if market is None or market is not self._sor_m1_observed_host:
            return
        read = getattr(market, "native_selected_observation_snapshot", None)
        if not callable(read):
            return
        code, interval, request_id = (event.get("stock_code"),
                                      event.get("timeframe_minutes"), event.get("request_id"))
        if not isinstance(code, str) or type(interval) is not int or not isinstance(request_id, str):
            return
        for key, window in tuple(self._open_windows.items()):
            session = self._validation_sessions.get(key)
            pool = self._historical_pools.get(key)
            if (not isinstance(session, ValidationSession) or not isinstance(pool, dict)
                    or self._v5_lease_hosts.get(key) is not market
                    or session.request.stock.code != code
                    or session.request.timeframe_minutes != interval
                    or session.request.timeframe_key != f"M{interval}"
                    or getattr(window, "stock", None) != session.request.stock
                    or not ((event.get("market_source") in {"NXT", "INTEGRATED"}
                             and self._validation_source_by_code.get(code) is True)
                            or (event.get("market_source") == "KRX"
                                and self._validation_source_by_code.get(code) is False
                                and code in self._validation_frozen_krx_codes))
                    or not self._window_accepts_results(window)):
                continue
            proof = read(key, code, interval, request_id)
            revision = proof.get("tick_revision", 0) if isinstance(proof, dict) else 0
            proof_key = (request_id, revision)
            if (not isinstance(proof, dict)
                    or self._native_selected_last.get(key) == proof_key
                    or event.get("market_source") != proof.get("market_source")
                    or event.get("market_data_identity") != proof.get("market_data_identity")
                    or event.get("forming_candle") != proof.get("forming_candle")
                    or event.get("completed_candles") != proof.get("completed_candles")
                    or not _v5_host_native_proof_is_current(
                        session.request.stock, proof, self._broker, self._now_factory())):
                continue
            source = proof["market_source"]
            if (pool.get("market_source") != source
                    or pool.get("market_data_identity") != proof["market_data_identity"]
                    or pool.get("stock") != session.request.stock
                    or pool.get("timeframe_key") != session.request.timeframe_key):
                run = self._last_run_requests.get(key)
                if (source == "INTEGRATED" and pool.get("market_source") == "NXT"
                        and proof.get("market_data_identity") == f"{code}_AL"
                        and key not in self._native_source_transition
                        and isinstance(run, IndicatorFollowSignalValidationRunRequest)):
                    self._native_source_transition[key] = deepcopy(event)
                    self._native_transition_nx_pools[key] = pool
                    self._native_transition_nx_bindings[key] = (
                        window, session, self._request_generation.get(key, 0))
                    next_run = IndicatorFollowSignalValidationRunRequest(
                        run.settings_snapshot, run.candle_count,
                        force_historical_refresh=False)
                    self._run_validation(window, next_run)
                continue
            verified_at = self._source_snapshot_datetime(pool.get("source_verified_at"))
            observed_at = datetime.fromisoformat(proof["request_receipt"][0]["request_as_of"])
            if verified_at is None or verified_at.date() != observed_at.date():
                # A new trading day needs a direct historical TR for yesterday's final bar.
                self._native_selected_pending.pop(key, None)
                run = self._last_run_requests.get(key)
                if isinstance(run, IndicatorFollowSignalValidationRunRequest):
                    self._run_validation(window, IndicatorFollowSignalValidationRunRequest(
                        run.settings_snapshot, run.candle_count,
                        force_historical_refresh=True))
                continue
            pending = self._native_selected_pending.get(key)
            base = pending[1] if pending and pending[0] is session else pool
            previous_proof = base.get("native_live_snapshot")
            if (isinstance(previous_proof, dict)
                    and (previous_proof.get("request_id"), previous_proof.get("tick_revision", 0))
                    == proof_key):
                continue
            delta = {row["time"]: row for row in base.get("native_live_completed", ())}
            delta.update({row["time"]: row for row in proof["completed_candles"]})
            batches = tuple(base.get("native_live_verified_batches", ()))
            if not revision:  # Only direct TR receipts belong in verified batches.
                frozen_proof = json.dumps(proof, ensure_ascii=False, sort_keys=True,
                                          separators=(",", ":"), allow_nan=False)
                batches = (*batches, frozen_proof)[-5000:]
            updated = dict(pool, native_live_snapshot=proof,
                           native_live_completed=tuple(delta[stamp] for stamp in sorted(delta)[-5000:]),
                           native_live_verified_batches=batches)
            self._native_selected_pending[key] = (session, updated,
                                                  self._request_generation.get(key, 0))
            self._publish_pending_native_selected(key)
            self._resume_cold_m1_source(key)

    def _native_selected_window_ready(self, window, key: int, *,
                                      require_base_ready: bool = True) -> bool:
        marking = getattr(window, "marking_display_check", None)
        marked = getattr(marking, "isChecked", None)
        busy = getattr(window, "is_chart_interaction_busy", None)
        return (
            (not require_base_ready or getattr(window, "_normal_base_ready", False))
            and getattr(window, "_normal_base_requested", False)
            and getattr(window, "_replay_snapshot", None) is None
            and getattr(window, "validation_range", None) is None
            and callable(marked) and not marked()
            and callable(busy) and not busy()
            and key not in self._range_replay_active
            and key not in self._history_browse_active
            and not getattr(window, "_marking_pool_publication_pending", False)
        )

    def _resume_cold_m1_source(self, key: int) -> bool:
        if key in self._prepare_active or key in self._prepare_pending:
            return False
        pending = self._native_selected_pending.get(key)
        window = self._open_windows.get(key)
        if pending is None or window is None or getattr(window, "_normal_base_ready", False):
            return False
        session, pool, generation = pending
        if (not isinstance(session, ValidationSession) or not isinstance(pool, dict)
                or session.request.timeframe_key != "M1"
                or self._validation_sessions.get(key) is not session
                or self._request_generation.get(key, 0) != generation
                or getattr(window, "stock", None) != session.request.stock
                or not self._window_accepts_results(window)
                or not self._native_selected_window_ready(
                    window, key, require_base_ready=False)):
            return False
        proof = pool.get("native_live_snapshot")
        code = session.request.stock.code
        current_pool = self._historical_pools.get(key)
        market = self._v5_lease_hosts.get(key)
        owner = self.parent()
        getter = getattr(owner, "existing_market_data_host", None)
        current_market = getter() if callable(getter) else None
        read = getattr(market, "native_selected_observation_snapshot", None)
        if (not isinstance(proof, dict) or proof.get("market_source") != "INTEGRATED"
                or proof.get("timeframe_minutes") != 1
                or pool.get("market_source") != "INTEGRATED"
                or pool.get("market_data_identity") != f"{code}_AL"
                or not isinstance(current_pool, dict)
                or current_pool.get("stock") != session.request.stock
                or current_pool.get("timeframe_key") != "M1"
                or current_pool.get("market_source") != "INTEGRATED"
                or current_pool.get("market_data_identity") != f"{code}_AL"
                or current_pool.get("request_id") != pool.get("request_id")
                or current_pool.get("candles") is not pool.get("candles")
                or self._validation_source_by_code.get(code) is not True
                or market is not self._sor_m1_observed_host
                or market is None or market is not current_market
                or not callable(read)
                or read(key, code, 1, proof.get("request_id")) != proof
                or not _v5_host_native_proof_is_current(
                    session.request.stock, proof, self._broker, self._now_factory())):
            return False
        from gui_market_data_host import MarketDataHost

        if not MarketDataHost._current_display_candles(market, code, 1):
            return False
        self._native_selected_pending.pop(key, None)
        self._use_pool_for_window(window, session, pool, evaluation_count=100)
        return True

    def _publish_pending_native_selected(self, key: int) -> None:
        pending = self._native_selected_pending.get(key)
        window = self._open_windows.get(key)
        if pending is None or window is None:
            return
        session, pool, generation = pending
        proof = pool["native_live_snapshot"]
        if (self._validation_sessions.get(key) is not session
                or self._request_generation.get(key, 0) != generation
                or not self._window_accepts_results(window)):
            self._native_selected_pending.pop(key, None)
            return
        if not self._native_selected_window_ready(window, key):
            return
        owner = self.parent()
        getter = getattr(owner, "existing_market_data_host", None)
        market = getter() if callable(getter) else None
        read = getattr(market, "native_selected_observation_snapshot", None)
        if (market is not self._sor_m1_observed_host or not callable(read)
                or read(key, session.request.stock.code,
                        session.request.timeframe_minutes, proof["request_id"]) != proof):
            self._native_selected_pending.pop(key, None)
            return
        try:
            view, deficit = self._normal_base_history_view(
                session, pool, self._history_targets.get(key, self._historical_count))
            if deficit:
                return
            self._native_selected_pending.pop(key, None)
            self._use_pool_for_window(window, session, pool, evaluation_count=100,
                                      _normal_view=view)
            self._native_selected_last[key] = (
                proof["request_id"], proof.get("tick_revision", 0))
        except ValueError:
            return

    def _live_sor_snapshot_is_current(self, window, session, pool, snapshot_key):
        code = session.request.stock.code
        key = id(window)
        owner = self.parent()
        getter = getattr(owner, "existing_market_data_host", None)
        market = getter() if callable(getter) else None
        forming = getattr(market, "_sor_forming_m1_by_stock", None)
        sealed = forming.get(code) if isinstance(forming, dict) else None
        candle = sealed.get("candle") if isinstance(sealed, dict) else None
        identity = snapshot_key[:2] if isinstance(snapshot_key, tuple) else ()
        current_session = getattr(market, "_current_display_session_identity", None)
        marking = getattr(window, "marking_display_check", None)
        marked = getattr(marking, "isChecked", None)
        busy = getattr(window, "is_chart_interaction_busy", None)
        return (
            isinstance(snapshot_key, tuple) and len(snapshot_key) == 4
            and self._open_windows.get(key) is window
            and self._validation_sessions.get(key) is session
            and self._historical_pools.get(key) is pool
            and self._sor_m1_last_snapshot.get(key) == snapshot_key
            and self._validation_source_by_code.get(code) is True
            and pool.get("market_source") == "INTEGRATED"
            and pool.get("market_data_identity") == f"{code}_AL"
            and pool.get("timeframe_key") == session.request.timeframe_key
            and session.request.timeframe_key in _LIVE_CALC_ACCEPTED_TIMEFRAMES
            and getattr(window, "stock", None) == session.request.stock
            and getattr(window, "_normal_base_ready", False)
            and getattr(window, "_replay_snapshot", None) is None
            and getattr(window, "validation_range", None) is None
            and callable(marked) and not marked()
            and callable(busy) and not busy()
            and self._window_accepts_results(window)
            and market is self._sor_m1_observed_host
            and callable(current_session) and current_session() == identity
            and isinstance(sealed, dict) and sealed.get("session_identity") == identity
            and isinstance(candle, dict)
            and candle.get("snapshot_source") in _V5_SOR_FORMING_SOURCES
            and (candle.get("bar_time"), candle.get("snapshot_received_at")) == snapshot_key[2:]
        )

    def _live_native_snapshot_is_current(
            self, window, session, pool, request_id, generation, prepared_view=None):
        key = id(window)
        proof = pool.get("native_live_snapshot") if isinstance(pool, dict) else None
        owner = self.parent()
        getter = getattr(owner, "existing_market_data_host", None)
        market = getter() if callable(getter) else None
        read = getattr(market, "native_selected_observation_snapshot", None)
        if (not isinstance(proof, dict) or not callable(read)
                or market is not self._sor_m1_observed_host
                or self._v5_lease_hosts.get(key) is not market
                or self._open_windows.get(key) is not window
                or self._validation_sessions.get(key) is not session
                or self._historical_pools.get(key) is not pool
                or self._request_generation.get(key, 0) != generation
                or getattr(window, "stock", None) != session.request.stock
                or not self._native_selected_window_ready(
                    window, key, require_base_ready=not (
                        session.request.timeframe_key == "M1"
                        and proof.get("market_source") == "INTEGRATED"))
                or proof.get("request_id") != request_id
                or proof.get("timeframe_minutes") != session.request.timeframe_minutes
                or pool.get("market_source") != proof.get("market_source")
                or pool.get("market_data_identity") != proof.get("market_data_identity")
                or read(key, session.request.stock.code,
                        session.request.timeframe_minutes, request_id) != proof
                or not _v5_host_native_proof_is_current(
                    session.request.stock, proof, self._broker, self._now_factory())
                or (isinstance(prepared_view, dict)
                    and prepared_view.get("native_forming_included") is True
                    and not _v5_host_native_forming_is_fresh(proof, self._now_factory()))):
            return False
        return True

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

    def _initialize_selected_stock_source(self, window: object, stock: ValidationStockRef) -> bool:
        try:
            initialize_from_verified_library(
                PROJECT_ROOT, stock.code, stock.name, at=self._now_factory(),
            )
        except (OSError, TypeError, ValueError) as exc:
            self._source_initialization_failed.add(id(window))
            self._invalidate_window_requests(id(window))
            self._fail_window(window, str(exc) or "NXT_ELIGIBILITY_NOT_VERIFIED")
            return False
        self._source_initialization_failed.discard(id(window))
        return True

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
        same_stock = (
            isinstance(current_stock, ValidationStockRef)
            and current_stock.code == selected.code
        )
        source_was_missing = same_stock and (
            window_key in self._source_initialization_failed
            or self._verified_nxt_validation_stock(selected) is None
        )
        if not self._initialize_selected_stock_source(window, selected):
            return
        if same_stock:
            self._sync_v5_observation_lease(window)
            if self._remember_stock(selected):
                self._refresh_open_window_stock_projections()
            if source_was_missing:
                request_validation = getattr(window, "request_normal_base", None)
                if callable(request_validation):
                    request_validation()
            return
        set_stock = getattr(window, "set_validation_stock", None)
        request_validation = getattr(window, "request_normal_base", None)
        if not callable(set_stock) or not callable(request_validation):
            self._fail_window(window, "SIGNAL_WINDOW_STOCK_API_UNAVAILABLE")
            return
        self._invalidate_window_requests(window_key)
        self._historical_pools.pop(window_key, None)
        self._validation_sessions.pop(window_key, None)
        self._sor_m1_last_snapshot.pop(window_key, None)
        self._history_targets[window_key] = self._historical_count
        self._history_extension_inflight.pop(window_key, None)
        self._history_extension_pending.pop(window_key, None)
        self._last_run_requests.pop(window_key, None)
        self._offsession_sor_m1_requests.pop(window_key, None)
        if set_stock(selected) is not True:
            return
        self._sync_v5_observation_lease(window)
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
        self._native_source_transition.pop(window_key, None)
        self._native_transition_nx_pools.pop(window_key, None)
        self._native_transition_nx_bindings.pop(window_key, None)
        self._broker_al_m1_request_receipts.pop(window_key, None)
        window = self._open_windows.get(window_key)
        disable_marking = getattr(window, "_disable_marking", None)
        if (
            callable(disable_marking)
            and not getattr(window, "_marking_policy_refresh_pending", False)
        ):
            disable_marking(clear_cache=True)
        self._clear_window_candle_preview(window_key, self._prepare_active.get(window_key))
        self._drop_deferred_prepare(window_key)
        self._drop_deferred_range(window_key)
        self._history_browse_active.pop(window_key, None)
        self._history_browse_indicator_cache.pop(window_key, None)
        self._history_browse_deferred.pop(window_key, None)
        self._complete_history_extension(window_key, success=False)
        self._history_extension_pending.pop(window_key, None)
        if window_key in self._request_generation:
            self._request_generation[window_key] += 1
        self._prepare_pending.pop(window_key, None)
        self._marking_source_revisions.pop(window_key, None)
        self._range_replay_pending.pop(window_key, None)
        self._active_run_identity.pop(window_key, None)
        self._filter_valid_evidence_pending.pop(window_key, None)
        if window_key in self._filter_valid_evidence_generation:
            self._filter_valid_evidence_generation[window_key] += 1
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
                # Pan/zoom can advance the interaction epoch without changing
                # the calculation source. Existing generation/token/pool
                # checks in the normal completion handler remain authoritative.
                self._prepare_deferred.pop(window_key, None)
                self._on_signal_scan_completed(deferred_prepare[1])
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
            return
        if window_key in self._native_selected_pending:
            self._publish_pending_native_selected(window_key)
            self._resume_cold_m1_source(window_key)
            if window_key in self._prepare_active:
                return
        pending_history = self._history_extension_pending.get(window_key)
        window = self._open_windows.get(window_key)
        if (
            pending_history is None
            or window is None
            or any(key[0] == window_key for key in self._active_providers)
            or self._window_busy_epoch(window) is not None
        ):
            return
        self._history_extension_pending.pop(window_key, None)
        needed = getattr(window, "_history_extension_is_needed", None)
        if (
            pending_history[0] == self._request_generation.get(window_key, 0)
            and pending_history[1] is self._last_run_requests.get(window_key)
            and callable(needed)
            and needed()
        ):
            self._request_history_extension(window)

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
        self._source_initialization_failed.discard(window_key)
        self._release_v5_observation_lease(window_key)
        self._broker_al_m1_request_receipts.pop(window_key, None)
        self._sor_m1_last_snapshot.pop(window_key, None)
        self._clear_window_candle_preview(window_key, self._prepare_active.get(window_key))
        self._open_windows.pop(window_key, None)
        self._request_generation.pop(window_key, None)
        self._prepare_active.pop(window_key, None)
        self._prepare_pending.pop(window_key, None)
        self._marking_source_revisions.pop(window_key, None)
        self._prepare_deferred.pop(window_key, None)
        self._range_replay_generation.pop(window_key, None)
        self._range_replay_active.pop(window_key, None)
        self._range_replay_pending.pop(window_key, None)
        self._range_replay_deferred.pop(window_key, None)
        self._active_run_identity.pop(window_key, None)
        self._filter_valid_evidence_generation.pop(window_key, None)
        self._filter_valid_evidence_active.pop(window_key, None)
        self._filter_valid_evidence_pending.pop(window_key, None)
        self._history_browse_active.pop(window_key, None)
        self._history_browse_indicator_cache.pop(window_key, None)
        self._history_browse_deferred.pop(window_key, None)
        self._market_snapshot_generation.pop(window_key, None)
        self._market_snapshot_metadata.pop(window_key, None)
        self._historical_pools.pop(window_key, None)
        self._validation_sessions.pop(window_key, None)
        self._history_targets.pop(window_key, None)
        inflight = self._history_extension_inflight.pop(window_key, None)
        self._history_extension_pending.pop(window_key, None)
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
            self._history_extension_pending[window_key] = (
                self._request_generation.get(window_key, 0), run_request,
            )
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
        eligibility = getattr(self, "_validation_source_by_code", {}).get(stock.code)
        if type(eligibility) is not bool:
            raise ValueError("NXT_ELIGIBILITY_NOT_VERIFIED")
        if not eligibility and stock.code not in getattr(
            self, "_validation_frozen_krx_codes", (),
        ):
            raise ValueError("SOR_SOURCE_UNAVAILABLE")
        if stock.code in self.__dict__.get("_validation_nxt_pre_codes", ()):
            return f"{stock.code}_NX"
        return f"{stock.code}_AL" if eligibility else stock.code

    def _validation_market_source(self, stock: ValidationStockRef) -> str:
        self._validation_market_data_identity(stock)
        if stock.code in self.__dict__.get("_validation_nxt_pre_codes", ()):
            return "NXT"
        return "INTEGRATED" if self._validation_source_by_code[stock.code] else "KRX"

    def _remember_native_receipt(self, started, response) -> None:
        requested = datetime.fromisoformat(started["request_as_of"]).astimezone(SEOUL_TIMEZONE)
        plan = read_stock_market_source_plan(PROJECT_ROOT, response["code"], requested.date())
        completed, forming = _v5_native_snapshot_parts(normalize_opt10080_rows(response["rows"]), plan,
                                               response["market_source"], response.get("interval", 1), requested,
                                               response.get("timeframe_key"))
        metadata = {key: value for key, value in response.items() if key != "rows"}
        metadata["native_forming_candle"] = forming
        self._native_minute_receipts[response["request_id"]] = (dict(started), metadata)
        self._native_minute_completed_content[response["request_id"]] = json.dumps(
            {"started": started, "metadata": metadata, "completed": completed},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

    def _historical_result_matches_validation_source(
        self,
        result: object,
        stock: ValidationStockRef,
    ) -> bool:
        if (
            not isinstance(result, ValidationHistoricalResult)
            or result.ok is not True
            or result.snapshot is None
            or (self._validation_source_by_code.get(stock.code) is False
                and stock.code not in getattr(self, "_validation_frozen_krx_codes", ()))
        ):
            return False
        expected_identity = self._validation_market_data_identity(stock)
        expected_source = self._validation_market_source(stock)
        response_identity = str(result.snapshot.market_data_identity or "").strip()
        response_source = str(result.snapshot.market_source or "").strip().upper()
        return response_identity == expected_identity and response_source == expected_source

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
    def _integrated_display_time_supported(
        candle: dict[str, object], timeframe_minutes: int = 1,
    ) -> bool:
        stamp = str(candle.get("time") or "")
        if len(stamp) != 14 or not stamp.isdigit():
            return False
        minute = stamp[8:12]
        return (
            "0800" <= minute < "0850"
            or "0900" <= minute < "1530"
            or "1530" <= minute < "2000"
        )

    @staticmethod
    def _verified_nxt_validation_stock(stock: ValidationStockRef) -> bool | None:
        try:
            plan = read_stock_market_source_plan(
                PROJECT_ROOT, stock.code, datetime.now(SEOUL_TIMEZONE).date(),
            )
        except (OSError, ValueError):
            return None
        return plan["nxt_available"] if plan is not None else None

    @staticmethod
    def _verified_nxt_display_stock(stock: ValidationStockRef) -> bool:
        try:
            plan = read_stock_market_source_plan(
                PROJECT_ROOT, stock.code, datetime.now(SEOUL_TIMEZONE).date(),
            )
        except ValueError:
            return False
        return plan is not None and plan["nxt_available"] is True

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
        if self._validation_source_by_code.get(session.request.stock.code) is not True:
            return
        sidecar = self._integrated_display_sidecars.get(
            self._integrated_display_sidecar_key(session)
        )
        if not self._verified_validation_premarket_candles(session, sidecar):
            return

        # Once the verified premarket source arrives, the normal V5 chart
        # must calculate its filters on the same source it presents. Reuse
        # the existing background normal-base worker, never scan in paint.
        key = id(window)
        pool = self._historical_pools.get(key)
        target = self._history_targets.get(key, self._historical_count)
        uncalculated = (
            self._open_windows.get(key) is window
            and self._validation_sessions.get(key) is session
            and getattr(window, "_normal_base_ready", False)
            and getattr(window, "_normal_base_requested", False)
            and getattr(window, "_replay_snapshot", None) is None
            and getattr(window, "validation_range", None) is None
            and not window.marking_display_check.isChecked()
            and not self._range_replay_active.get(key)
            and not self._history_browse_active.get(key)
            and isinstance(pool, dict)
        )
        if uncalculated:
            view, deficit = self._normal_base_history_view(
                session, pool, target,
            )
            current = getattr(window, "_calculation_candles", None)
            if (
                view.get("validation_composite_calculation") is True
                and isinstance(current, list)
                and current != view["candles"]
            ):
                # Reuse the normal worker and its warmup path.
                self._use_pool_for_window(
                    window, session, pool, evaluation_count=100,
                    _normal_view=view if deficit == 0 else None,
                )
                return
        if (
            window.marking_display_check.isChecked()
            or getattr(window, "validation_range", None) is not None
            or getattr(window, "_replay_snapshot", None) is not None
        ):
            # Preserve frozen evidence: never show a newer NXT source
            # under an older marking/range replay without recomputation.
            trace_signal_validation_event(
                "flow.integrated_display.pending_active_evidence",
                window_key=key, timeframe=session.request.timeframe_key,
            )
            if (
                window.marking_display_check.isChecked()
                and getattr(window, "validation_range", None) is None
                and getattr(window, "_replay_snapshot", None) is None
            ):
                self._start_marking_source_revision(window, session)
            return
        # No display-only publish: calculation and chart share one source.

    @staticmethod
    def _marking_revision_candle_identity(candles: object) -> str:
        if not isinstance(candles, list) or not candles:
            return ""
        return hashlib.sha256(json.dumps(
            candles, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest()

    def _start_marking_source_revision(
        self,
        window: object,
        session: ValidationSession,
        *,
        candidate_view_pool: dict[str, object] | None = None,
        normal_base_presentation: object | None = None,
    ) -> bool:
        """Prepare one source-bound marking revision without hiding old evidence."""
        window_key = id(window)
        pool = self._historical_pools.get(window_key)
        sidecar_key = self._integrated_display_sidecar_key(session)
        integrated_revision = candidate_view_pool is None
        sidecar = (
            self._integrated_display_sidecars.get(sidecar_key)
            if integrated_revision else None
        )
        settings = getattr(window, "_marking_settings", None)
        times = getattr(window, "_marking_times", None)
        old_key = getattr(window, "_marking_key", None)
        old_snapshot = getattr(window, "_marking_snapshot", None)
        old_comparison = getattr(window, "_marking_comparison_cache", None)
        generation = getattr(window, "_marking_generation", None)
        source_start = getattr(window, "_marking_source_start", None)
        if (
            self._open_windows.get(window_key) is not window
            or self._validation_sessions.get(window_key) is not session
            or not isinstance(pool, dict)
            or (integrated_revision and not isinstance(sidecar, dict))
            or (
                not integrated_revision
                and not isinstance(candidate_view_pool, dict)
            )
            or not isinstance(settings, ValidationSettingsSnapshot)
            or not isinstance(times, tuple) or len(times) != 2
            or not all(str(value or "") for value in times)
            or old_key is None
            or not isinstance(old_snapshot, ValidationReplaySnapshot)
            or old_comparison is None
            or isinstance(generation, bool) or not isinstance(generation, int)
            or not str(source_start or "")
            or (
                integrated_revision
                and (
                    sidecar.get("verified_nxt_eligible") is not True
                    or sidecar.get("market_data_identity")
                    != f"{session.request.stock.code}_AL"
                    or sidecar.get("market_source") != "INTEGRATED"
                )
            )
        ):
            trace_signal_validation_event(
                "flow.marking_source_revision.blocked",
                window_key=window_key,
                reason="IDENTITY_OR_VERIFICATION_UNAVAILABLE",
            )
            return False
        request_generation = self._request_generation.get(window_key, 0)
        history_target = self._history_targets.get(
            window_key, self._historical_count,
        )
        rules = settings.to_dict()
        regular_only = bool(
            window.validation_regular_market_only_check.isChecked()
        )
        descriptors = tuple(window._filter_display_descriptors(rules))
        source_calculation = (
            candidate_view_pool.get("candles")
            if isinstance(candidate_view_pool, dict) else None
        )
        source_display = (
            candidate_view_pool.get("display_candles", source_calculation)
            if isinstance(candidate_view_pool, dict) else None
        )
        if isinstance(source_display, list):
            source_display = source_display[-history_target:]
        sidecar_candles = (
            sidecar.get("candles") if isinstance(sidecar, dict) else None
        )
        try:
            calculation_content = self._marking_revision_candle_identity(
                source_calculation
            )
            display_content = self._marking_revision_candle_identity(
                source_display
            )
            sidecar_content = (
                self._marking_revision_candle_identity(sidecar_candles)
                if integrated_revision else ""
            )
        except (TypeError, ValueError, OverflowError):
            calculation_content = ""
            display_content = ""
            sidecar_content = ""
        if (
            (integrated_revision and not sidecar_content)
            or (
                not integrated_revision
                and (not calculation_content or not display_content)
            )
        ):
            trace_signal_validation_event(
                "flow.marking_source_revision.blocked",
                window_key=window_key,
                reason=(
                    "SIDECAR_CONTENT_INVALID"
                    if integrated_revision else "CANDIDATE_CONTENT_INVALID"
                ),
            )
            return False
        source_identity = (
            (
                sidecar.get("market_data_identity"),
                sidecar.get("market_source"),
                sidecar.get("request_as_of"),
                sidecar.get("coverage_count"),
                sidecar_content,
                True,
            )
            if integrated_revision
            else (
                "CANONICAL",
                calculation_content,
                display_content,
                candidate_view_pool.get("display_only_integrated") is True,
            )
        )
        revision_identity = (
            session.request.stock.code,
            session.request.timeframe_key,
            session.request.timeframe_minutes,
            settings.rules_hash,
            id(session),
            self._pool_computation_source_identity(pool),
            source_identity,
            request_generation,
            generation,
            times,
            source_start,
            old_key,
        )
        existing = self._marking_source_revisions.get(window_key)
        if (
            isinstance(existing, dict)
            and existing.get("identity") == revision_identity
            and (
                (
                    existing.get("status") == "preparing"
                    and existing.get("pool") is pool
                )
                or (
                    existing.get("status") == "published"
                    and integrated_revision
                )
            )
        ):
            trace_signal_validation_event(
                "flow.marking_source_revision.deduplicated",
                window_key=window_key,
                status=str(existing.get("status") or ""),
            )
            return True
        if (
            isinstance(existing, dict)
            and existing.get("identity") == revision_identity
            and existing.get("status") == "published"
            and not integrated_revision
        ):
            complete = getattr(
                window,
                "complete_pending_marking_source_publication",
                None,
            )
            if callable(complete) and complete(
                generation=generation,
                key=old_key,
                times=times,
                settings=settings,
            ):
                trace_signal_validation_event(
                    "flow.marking_source_revision.reused",
                    window_key=window_key,
                )
                return True
        token = object()
        if isinstance(existing, dict) and existing.get("status") == "preparing":
            existing["status"] = "cancelled"
            existing["reason"] = "SUPERSEDED"
        state = {
            "identity": revision_identity,
            "token": token,
            "status": "preparing",
            "sidecar": sidecar,
            "sidecar_content": sidecar_content,
            "source_kind": "integrated" if integrated_revision else "canonical",
            "calculation_content": calculation_content,
            "display_content": display_content,
            "candidate_installed": (
                not integrated_revision
                or bool(getattr(window, "_marking_pool_publication_pending", False))
            ),
            "pool": pool,
        }
        self._marking_source_revisions[window_key] = state
        worker_pool = deepcopy(pool)
        worker_sidecar = deepcopy(sidecar) if integrated_revision else None
        worker_view = (
            deepcopy(candidate_view_pool) if not integrated_revision else None
        )
        marking_source_identity = revision_identity[5]

        def current() -> bool:
            active = self._marking_source_revisions.get(window_key)
            current_sidecar = (
                self._integrated_display_sidecars.get(sidecar_key)
                if integrated_revision else None
            )
            return (
                isinstance(active, dict)
                and active.get("token") is token
                and active.get("identity") == revision_identity
                and self._open_windows.get(window_key) is window
                and self._request_generation.get(window_key, 0)
                == request_generation
                and self._history_targets.get(
                    window_key,
                    self._historical_count,
                ) == history_target
                and self._validation_sessions.get(window_key) is session
                and self._historical_pools.get(window_key) is pool
                and getattr(window, "_marking_generation", None) == generation
                and getattr(window, "_marking_key", None) == old_key
                and getattr(window, "_marking_times", None) == times
                and getattr(window, "_marking_settings", None) is settings
                and getattr(window, "_marking_source_start", None) == source_start
                and getattr(window, "_marking_snapshot", None) is old_snapshot
                and getattr(window, "_marking_comparison_cache", None)
                is old_comparison
                and (
                    not integrated_revision
                    or current_sidecar is sidecar
                )
            )

        def cancel_state(reason: str) -> None:
            active = self._marking_source_revisions.get(window_key)
            if isinstance(active, dict) and active.get("token") is token:
                active["status"] = "cancelled"
                active["reason"] = reason
                trace_signal_validation_event(
                    "flow.marking_source_revision.cancelled",
                    window_key=window_key,
                    reason=reason,
                )

        def prepare() -> None:
            payload = {
                "source_revision": True,
                "window_key": window_key,
                "revision_token": token,
                "revision_identity": revision_identity,
                "generation": generation,
                "key": old_key,
                "request_generation": request_generation,
                "session": session,
                "pool": pool,
                "history_target": history_target,
                "times": times,
                "source_start": source_start,
                "settings": settings,
                "snapshot": None,
                "comparison": None,
                "error": "",
            }

            def emit_completion() -> None:
                try:
                    self.marking_completed.emit(payload)
                except RuntimeError:
                    cancel_state("FLOW_DESTROYED")

            try:
                if not current():
                    cancel_state("STALE_BEFORE_PREPARE")
                    payload["error"] = "MARKING_SOURCE_REVISION_STALE_BEFORE_PREPARE"
                    payload["cancelled"] = True
                    emit_completion()
                    return
                # Bind preparation to the accepted canonical candidate or the
                # exact verified integrated sidecar captured for this revision.
                if worker_view is not None:
                    view, deficit = worker_view, 0
                else:
                    view, deficit = self._normal_base_history_view(
                        session,
                        worker_pool,
                        history_target,
                        sidecar_override=worker_sidecar,
                    )
                if self._verified_sor_tail_provider is not None:
                    now = self._now_factory()
                    now = (now.replace(tzinfo=SEOUL_TIMEZONE) if now.tzinfo is None
                           else now.astimezone(SEOUL_TIMEZONE))
                    view = self._verified_sor_view_for_session(
                        session, worker_pool, view, now, marking_source_identity,
                    )
                if deficit:
                    raise ValueError(
                        f"MARKING_SOURCE_REVISION_HISTORY_INSUFFICIENT: missing={deficit}"
                    )
                calculation = view.get("candles")
                display = view.get("display_candles", calculation)
                if not isinstance(calculation, list) or not calculation:
                    raise ValueError("MARKING_SOURCE_REVISION_CALCULATION_UNAVAILABLE")
                if not isinstance(display, list) or not display:
                    raise ValueError("MARKING_SOURCE_REVISION_DISPLAY_UNAVAILABLE")
                display = display[-history_target:]
                display_integrated = view.get("display_only_integrated") is True
                indexes = (
                    _calculation_indexes_for_mixed_display(calculation, display)
                    if display_integrated
                    else _calculation_indexes_for_display(calculation, display)
                )
                candidate_display = [
                    candle for candle in display
                    if times[0] <= str(candle.get("time") or "") <= times[1]
                ]
                candidate_key = window._marking_source_key_for_candles(
                    window.stock.code,
                    settings,
                    times,
                    regular_only=regular_only,
                    calculation_candles=calculation,
                    display_candles=candidate_display,
                    prefix_start=source_start,
                )
                if candidate_key == old_key:
                    snapshot = old_snapshot
                    comparison = old_comparison
                    recalculated = False
                else:
                    result = self._prepare_replay_result_from_pool(
                        session,
                        view,
                        history_target=history_target,
                        chart_range=(0, 0),
                        chart_range_times=times,
                        prepared_view_pool=view,
                        cancel_predicate=lambda: not current(),
                    )
                    if result.ok is not True or result.snapshot is None:
                        raise ValueError(self._failure_text(
                            "MARKING_SOURCE_REVISION", result,
                        ))
                    snapshot = result.snapshot
                    rows = snapshot.to_candles()
                    comparison_cache, _ = _build_validation_projection_data(
                        rows,
                        snapshot.to_entries(),
                        [],
                        tuple(range(len(rows))),
                        rules,
                        tuple(
                            descriptor for descriptor in descriptors
                            if descriptor.family == FAMILY_PRICE_COMPARISON
                        ),
                        cancel_predicate=lambda: not current(),
                    )
                    comparison = (
                        tuple(str(row.get("time") or "") for row in rows),
                        comparison_cache,
                    )
                    recalculated = True
                prepared_normal = normal_base_presentation
                if (
                    isinstance(prepared_normal, tuple)
                    and len(prepared_normal) == 2
                    and not integrated_revision
                ):
                    normal_presentation = prepared_normal
                else:
                    normal_cache, _ = _build_validation_projection_data(
                        calculation,
                        [],
                        [],
                        indexes,
                        rules,
                        descriptors,
                        cancel_predicate=lambda: not current(),
                    )
                    normal_presentation = (descriptors, normal_cache)
                if not current():
                    cancel_state("STALE_AFTER_PREPARE")
                    payload["error"] = "MARKING_SOURCE_REVISION_STALE_AFTER_PREPARE"
                    payload["cancelled"] = True
                    emit_completion()
                    return
                payload.update(
                    calculation=calculation,
                    display=display,
                    display_only_integrated=display_integrated,
                    candidate_key=candidate_key,
                    candidate_display=candidate_display,
                    snapshot=snapshot,
                    comparison=comparison,
                    normal_base_presentation=normal_presentation,
                    calculation_identity=self._pool_computation_source_identity(view),
                    calculation_content=self._marking_revision_candle_identity(
                        calculation
                    ),
                    display_identity=self._marking_revision_candle_identity(display),
                    recalculated=recalculated,
                )
            except Exception as exc:
                payload["error"] = str(exc)
            if not current():
                cancel_state("STALE_AFTER_ERROR")
                payload["error"] = (
                    payload["error"]
                    or "MARKING_SOURCE_REVISION_STALE_AFTER_ERROR"
                )
                payload["cancelled"] = True
                emit_completion()
                return
            emit_completion()

        trace_signal_validation_event(
            "flow.marking_source_revision.started",
            window_key=window_key,
            request_generation=request_generation,
            marking_generation=generation,
            timeframe=session.request.timeframe_key,
            frozen_times=times,
        )
        try:
            Thread(
                target=prepare,
                name="indicator-follow-v5-marking-source-revision",
                daemon=True,
            ).start()
        except Exception as exc:
            state["status"] = "error"
            state["error"] = str(exc)
            trace_signal_validation_event(
                "flow.marking_source_revision.failed",
                window_key=window_key,
                reason=str(exc),
            )
            if state.get("candidate_installed") is True:
                fail = getattr(window, "fail_marking_source_revision", None)
                if callable(fail):
                    fail(
                        generation=generation,
                        previous_key=old_key,
                        times=times,
                        settings=settings,
                        error=exc,
                    )
            else:
                window.marking_display_check.setToolTip(str(exc))
            return False
        return True

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
            or session.request.stock.code in self.__dict__.get("_validation_nxt_pre_codes", ())
            or self._validation_source_by_code.get(session.request.stock.code) is not True
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
            and self._source_snapshot_reusable(
                cached.get("request_as_of"), require_same_date=True,
            )
        ):
            self._apply_integrated_display_sidecar(window, session)
            return
        if self._integrated_display_inflight.get(key, 0) >= request_count:
            return
        self._integrated_display_inflight[key] = request_count
        stock = session.request.stock
        window_key = id(window)
        # Bind admissibility to the TR request snapshot, not callback arrival
        # or subsequent GUI refresh time. An open target bucket may include
        # the current forming M1 price.
        try:
            request_as_of = self._source_snapshot_datetime(self._now_factory())
        except Exception:
            request_as_of = None
        if request_as_of is None:
            self._integrated_display_inflight.pop(key, None)
            return

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
                if self._integrated_display_time_supported(
                    candle, session.request.timeframe_minutes,
                )
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
            existing_as_of = self._source_snapshot_datetime(
                existing.get("request_as_of") if isinstance(existing, dict) else None
            )
            if (
                existing_as_of is None
                or request_as_of > existing_as_of
                or (request_as_of == existing_as_of and coverage_count >= existing_coverage)
            ):
                self._integrated_display_sidecars[key] = {
                    "stock_code": stock.code,
                    "timeframe_key": session.request.timeframe_key,
                    "timeframe_minutes": session.request.timeframe_minutes,
                    "requested_count": request_count,
                    "coverage_count": coverage_count,
                    "market_data_identity": f"{stock.code}_AL",
                    "market_source": "INTEGRATED",
                    "verified_nxt_eligible": True,
                    "display_only": True,
                    "request_as_of": request_as_of.isoformat(),
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
    def _signal_view_signature(
        pool: object,
        *,
        source_identity: tuple[object, ...] | None = None,
    ) -> tuple[object, ...] | None:
        if not isinstance(pool, dict):
            return None
        candles = pool.get("candles")
        if not isinstance(candles, list) or not candles:
            return None
        if source_identity is None:
            source_identity = (
                IndicatorFollowSignalValidationV5Flow._pool_computation_source_identity(
                    pool
                )
            )
        candle_content_identity = (
            source_identity[-1]
            if isinstance(source_identity, tuple) and len(source_identity) >= 9
            else ""
        )
        return (
            str(pool.get("timeframe_key") or ""),
            len(candles),
            str(candles[0].get("time") or ""),
            str(candles[-1].get("time") or ""),
            (
                candle_content_identity,
                pool.get("production_calculation_cache_identity"),
                pool.get("validation_composite_content_hash")
                or pool.get("validation_premarket_content_hash"),
                *((pool["sor_tail_signature"],)
                  if "sor_tail_signature" in pool else ()),
            ),
        )

    @staticmethod
    def _source_snapshot_datetime(value: object) -> datetime | None:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str) and value:
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                return None
        else:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(SEOUL_TIMEZONE)

    def _source_snapshot_reusable(
        self,
        verified_at: object,
        *,
        require_same_date: bool = False,
    ) -> bool:
        verified = self._source_snapshot_datetime(verified_at)
        try:
            current = self._source_snapshot_datetime(self._now_factory())
        except Exception:
            current = None
        if verified is None or current is None or current < verified:
            return False
        if current.date() == verified.date():
            if current.weekday() >= 5:
                return True
            return current.replace(second=0, microsecond=0) == verified.replace(
                second=0, microsecond=0,
            )
        if require_same_date:
            return False
        cursor = verified.date()
        while cursor <= current.date():
            if cursor.weekday() < 5:
                return False
            cursor += timedelta(days=1)
        return True

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
            and pool.get("market_source") == self._validation_market_source(stock)
            and isinstance(pool.get("requested_count"), int)
            and int(pool["requested_count"]) >= required_count
            and isinstance(pool.get("candles"), list)
            and bool(pool["candles"])
            and self._source_snapshot_reusable(pool.get("source_verified_at"))
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
        if pool.get("market_source") != self._validation_market_source(stock):
            return "market_source"
        requested_count = pool.get("requested_count")
        if not isinstance(requested_count, int) or requested_count < required_count:
            return "requested_count"
        candles = pool.get("candles")
        if not isinstance(candles, list) or not candles:
            return "candles"
        if not self._source_snapshot_reusable(pool.get("source_verified_at")):
            return "freshness"
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
        if entry.market_source == "NXT":
            return True
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

    @staticmethod
    def _direct_selected_completion_time(row: dict[str, object], minutes: int):
        from gui_market_data_host import MarketDataHost, _DISPLAY_INTEGRATED_SESSIONS

        start = candle_market_datetime(row)
        if start is None or start.second:
            return None
        session_name = MarketDataHost._display_session_for_time(start)
        span = next(((first, end) for name, first, end in
                     _DISPLAY_INTEGRATED_SESSIONS if name == session_name), None)
        if span is None and session_name == "AL_TRANSITION" and minutes in (
                3, 5, 10, 15, 30, 60, 120, 240):
            session_end = 15 * 60 + 40
        elif span is not None:
            session_end = span[1]
        else:
            return None
        return min(start + timedelta(minutes=minutes),
                   start.replace(hour=session_end // 60, minute=session_end % 60))

    @classmethod
    def _direct_selected_query_valid(cls, query, stock, key, minutes, identity, candles):
        if not isinstance(query, dict) or not candles or not isinstance(candles[-1], dict):
            return False
        as_of = cls._source_snapshot_datetime(query.get("request_as_of"))
        completed_at = cls._direct_selected_completion_time(candles[-1], minutes)
        return (query.get("stock_code") == stock.code
                and query.get("timeframe_key") == key
                and query.get("timeframe_minutes") == minutes
                and query.get("market_data_identity") == identity
                and query.get("market_source") == "INTEGRATED"
                and isinstance(query.get("request_id"), str)
                and bool(query["request_id"])
                and query.get("last_completed_time") == candles[-1].get("time")
                and as_of is not None and completed_at is not None
                and completed_at <= as_of)

    def _direct_selected_history_parts(self, snapshot):
        candles, _ = project_validation_candles(snapshot)
        candles = self._without_untraded_direct_d1_tail(
            candles, snapshot.timeframe_key, snapshot.market_source,
            snapshot.market_data_identity, snapshot.stock.code,
            snapshot.request_as_of,
        )
        if (snapshot.timeframe_key == "D1" and snapshot.market_source == "INTEGRATED"
                and snapshot.market_data_identity == f"{snapshot.stock.code}_AL"
                and candles):
            selected_at = self._source_snapshot_datetime(snapshot.request_as_of)
            last = candles[-1]
            if (selected_at is not None and isinstance(last, dict)
                    and str(last.get("time") or "") ==
                        selected_at.strftime("%Y%m%d") + "000000"):
                return candles, None, {
                    "stock_code": snapshot.stock.code,
                    "timeframe_key": "D1", "timeframe_minutes": snapshot.timeframe_minutes,
                    "market_data_identity": snapshot.market_data_identity,
                    "market_source": snapshot.market_source,
                    "request_id": snapshot.request_id,
                    "request_as_of": selected_at.isoformat(),
                    "selected_row_sha256": hashlib.sha256(json.dumps(
                        last, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":"), allow_nan=False,
                    ).encode("utf-8")).hexdigest(),
                }
        if (snapshot.market_source != "INTEGRATED"
                or snapshot.timeframe_key not in _LIVE_MINUTE_TIMEFRAMES
                or (snapshot.timeframe_key == "M1" and snapshot.stock.code != "005930")):
            return candles, None, None
        as_of = self._source_snapshot_datetime(snapshot.request_as_of)
        if as_of is None or not candles or not isinstance(candles[-1], dict):
            raise ValueError("SOR_DIRECT_QUERY_TIME_UNAVAILABLE")
        if (snapshot.market_data_identity != f"{snapshot.stock.code}_AL"
                or snapshot.timeframe_key != f"M{snapshot.timeframe_minutes}"):
            raise ValueError("SOR_DIRECT_QUERY_IDENTITY_INVALID")
        end = self._direct_selected_completion_time(
            candles[-1], snapshot.timeframe_minutes)
        if end is None:
            raise ValueError("SOR_DIRECT_HISTORY_PROVENANCE_INVALID")
        forming = None
        if snapshot.timeframe_key == "M1":
            # The replay projector drops query-time M1. Parse that immutable
            # selected response separately, then judge completion at the real
            # request time below; it never enters completed history.
            selected, _ = project_validation_candles(
                snapshot, as_of=as_of + timedelta(minutes=1))
            if len(selected) == len(candles) + 1 and selected[:-1] == candles:
                candidate = selected[-1]
                candidate_start = candle_market_datetime(candidate)
                candidate_end = self._direct_selected_completion_time(candidate, 1)
                if (candidate_start is None or candidate_end is None
                        or not candidate_start <= as_of < candidate_end):
                    raise ValueError("SOR_DIRECT_HISTORY_PROVENANCE_INVALID")
                forming = {
                    "stock_code": snapshot.stock.code,
                    "timeframe_key": "M1", "timeframe_minutes": 1,
                    "market_data_identity": snapshot.market_data_identity,
                    "market_source": snapshot.market_source,
                    "request_id": snapshot.request_id,
                    "request_as_of": as_of.isoformat(),
                    "candle": candidate,
                }
        if end > as_of:
            start = candle_market_datetime(candles[-1])
            if start is None or start > as_of:
                raise ValueError("SOR_DIRECT_HISTORY_PROVENANCE_INVALID")
            forming = {
                "stock_code": snapshot.stock.code,
                "timeframe_key": snapshot.timeframe_key,
                "timeframe_minutes": snapshot.timeframe_minutes,
                "market_data_identity": snapshot.market_data_identity,
                "market_source": snapshot.market_source,
                "request_id": snapshot.request_id,
                "request_as_of": as_of.isoformat(),
                "candle": candles[-1],
            }
            candles = candles[:-1]
        if not candles:
            raise ValueError("SOR_DIRECT_HISTORY_UNAVAILABLE")
        query = {
            "stock_code": snapshot.stock.code,
            "timeframe_key": snapshot.timeframe_key,
            "timeframe_minutes": snapshot.timeframe_minutes,
            "market_data_identity": snapshot.market_data_identity,
            "market_source": snapshot.market_source,
            "request_id": snapshot.request_id,
            "request_as_of": as_of.isoformat(),
            "last_completed_time": candles[-1]["time"],
            **({"source_content_sha256": hashlib.sha256(json.dumps(
                candles, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")).hexdigest()}
               if snapshot.timeframe_key == "M1" else {}),
            **({"selected_forming_sha256": hashlib.sha256(json.dumps(
                forming["candle"], ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")).hexdigest()}
               if snapshot.timeframe_key == "M1" and forming is not None else {}),
        }
        if not self._direct_selected_query_valid(
                query, snapshot.stock, snapshot.timeframe_key,
                snapshot.timeframe_minutes, snapshot.market_data_identity, candles):
            raise ValueError("SOR_DIRECT_HISTORY_PROVENANCE_INVALID")
        return candles, forming, query

    @classmethod
    def _without_untraded_direct_d1_tail(
        cls, candles, timeframe_key, market_source, identity, stock_code, verified_at,
    ):
        at = cls._source_snapshot_datetime(verified_at)
        if (timeframe_key == "D1" and market_source == "INTEGRATED"
                and identity == f"{stock_code}_AL" and at is not None and candles
                and isinstance(candles[-1], dict)
                and candles[-1].get("time") == at.strftime("%Y%m%d") + "000000"
                and candles[-1].get("volume") == 0
                and len({candles[-1].get(field) for field in
                         ("open", "high", "low", "close")}) == 1):
            return candles[:-1]
        return candles

    def _cached_history(
        self,
        stock: ValidationStockRef,
        timeframe_minutes: int,
        timeframe_key: str,
        fetch_count: int,
    ) -> ValidationHistoricalCacheEntry | None:
        if (stock.code == "005930" and timeframe_key == "M1"
                and self._validation_source_by_code.get(stock.code) is True):
            from gui_market_data_host import MarketDataHost

            now = self._now_factory()
            now = (now.replace(tzinfo=SEOUL_TIMEZONE) if now.tzinfo is None
                   else now.astimezone(SEOUL_TIMEZONE))
            if (now.weekday() >= 5 or MarketDataHost._display_session_for_time(now)
                    not in {"NXT_PRE", "KRX", "NXT_AFTER"}):
                return None  # A prior cache has no current selected-response proof.
        if not any(callable(getattr(self._historical_cache, name, None))
                   for name in ("load_covering", "load")):
            return None
        return self._cached_history_for_source(
            stock, timeframe_minutes, timeframe_key, fetch_count,
            self._validation_market_data_identity(stock),
            self._validation_market_source(stock),
        )

    def _cached_history_for_source(
        self, stock, timeframe_minutes, timeframe_key, fetch_count,
        expected_identity, expected_source, *, legacy_display_only=False,
    ) -> ValidationHistoricalCacheEntry | None:
        """Read a direct-source cache with its existing identity/content proof."""
        load_covering = getattr(self._historical_cache, "load_covering", None)
        load = getattr(self._historical_cache, "load", None)
        if not callable(load_covering) and not callable(load):
            return None
        source_kwargs = {"market_data_identity": expected_identity}
        try:
            entry = (
                load_covering(
                    stock.code,
                    timeframe_minutes,
                    fetch_count,
                    timeframe_key,
                    **source_kwargs,
                )
                if callable(load_covering)
                else load(
                    stock.code,
                    timeframe_minutes,
                    fetch_count,
                    timeframe_key,
                    **source_kwargs,
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
            market_data_identity=expected_identity,
        )
        marker_identity = ""
        marker_source = ""
        marker_stock = ""
        marker_hash = ""
        if marker_path is not None:
            try:
                marker = json.loads(marker_path.read_text(encoding="utf-8"))
                if isinstance(marker, dict):
                    marker_identity = str(marker.get("market_data_identity") or "").strip()
                    marker_source = str(marker.get("market_source") or "").strip().upper()
                    marker_stock = str(marker.get("canonical_stock_code") or "").strip()
                    marker_hash = str(marker.get("candle_content_sha256") or "").strip()
            except (OSError, ValueError, TypeError):
                pass
        entry_identity = str(entry.market_data_identity or "").strip()
        if not (
            entry_identity == expected_identity
            and str(entry.market_source or "").strip().upper()
            == expected_source
            and marker_identity == expected_identity
            and marker_stock == stock.code
            and marker_source == expected_source
        ):
            return None
        content = json.dumps(
            list(entry.candles), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        if marker_hash != hashlib.sha256(content).hexdigest():
            return None
        if timeframe_key == "D1" and entry.market_source == "INTEGRATED":
            completed = self._without_untraded_direct_d1_tail(
                list(entry.candles), timeframe_key, entry.market_source,
                entry.market_data_identity, stock.code, entry.updated_at,
            )
            if len(completed) != len(entry.candles):
                entry = replace(entry, candles=tuple(completed))
        if (entry.market_source == "INTEGRATED"
                and timeframe_key in _LIVE_MINUTE_TIMEFRAMES - {"M1"}):
            query = marker.get("direct_selected_query") if isinstance(marker, dict) else None
            if query is None:
                stamps = [str(row.get("time") or "") for row in entry.candles
                          if isinstance(row, dict)]
                if (not legacy_display_only or len(stamps) != len(entry.candles)
                        or stamps != sorted(set(stamps))):
                    return None
                # Legacy AL data may supply prior display only. Its final row
                # has no immutable completion proof and never enters this view.
                entry = replace(entry, candles=entry.candles[:-1])
            elif not self._direct_selected_query_valid(
                    query, stock, timeframe_key, timeframe_minutes,
                    expected_identity, list(entry.candles)):
                return None
        if entry.market_source == "NXT" and not self._nx_cached_rows_valid(
                stock, timeframe_minutes, timeframe_key, list(entry.candles),
                marker.get("native_completed_receipt")):
            return None
        return entry

    def _nx_cached_rows_valid(self, stock, minutes, key, candles, proof,
                              *, retained_at: datetime | None = None) -> bool:
        """Persistent NX proof admits completed history, never a live forming row."""
        try:
            now = (retained_at or self._now_factory()).astimezone(SEOUL_TIMEZONE)
            plan = read_stock_market_source_plan(PROJECT_ROOT, stock.code, now.date())
            selected = source_for_time(plan, now) if plan else None
            if (not isinstance(proof, dict) or not candles or selected is None
                    or selected["source"] != "NXT" or plan["stock_name"] != stock.name
                    or proof.get("stock_code") != stock.code or proof.get("timeframe_key") != key
                    or proof.get("market_data_identity") != f"{stock.code}_NX"
                    or proof.get("market_source") != "NXT" or proof.get("completed_only") is not True
                    or type(proof.get("connection_epoch")) is not int or proof["connection_epoch"] <= 0
                    or not isinstance(proof.get("request_id"), str) or not proof["request_id"].strip()
                    or not isinstance(proof.get("session_sha256"), str)
                    or len(proof["session_sha256"]) != 64
                    or any(ch not in "0123456789abcdef" for ch in proof["session_sha256"])):
                return False
            requested = datetime.fromisoformat(proof["request_as_of"])
            received = datetime.fromisoformat(proof["received_at"])
            if (requested.tzinfo is None or received.tzinfo is None
                    or not requested <= received <= now or received - requested >= timedelta(minutes=1)):
                return False
            original_plan = read_stock_market_source_plan(PROJECT_ROOT, stock.code, requested.date())
            if original_plan is None:
                return False
            for at in (requested, received):
                source = source_for_time(original_plan, at)
                if source is None or source["source"] != "NXT":
                    return False
            completed, forming = _v5_native_snapshot_parts(candles, original_plan, "NXT", minutes, requested, key)
            digest = hashlib.sha256(json.dumps(candles, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
            return forming is None and completed == candles and proof.get("source_content_sha256") == digest
        except (ValueError, TypeError, KeyError, AttributeError, OSError):
            return False

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
            identity = self._validation_market_data_identity(stock)
            source_kwargs = {"market_data_identity": identity}
            payloads = load(
                stock.code,
                timeframe_minutes,
                fetch_count,
                settings_hash,
                timeframe_key,
                **source_kwargs,
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
            identity = self._validation_market_data_identity(stock)
            source_kwargs = {"market_data_identity": identity}
            return store(
                stock_code=stock.code,
                timeframe_minutes=timeframe_minutes,
                requested_count=fetch_count,
                settings_hash=settings_hash,
                entries=payloads,
                timeframe_key=timeframe_key,
                **source_kwargs,
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
        direct_m1_receipt: dict[str, object] | None = None,
        native_source_receipt: object = None,
        direct_selected_query: dict[str, object] | None = None,
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
        persisted_source = str(market_source or "").strip()
        expected_identity = self._validation_market_data_identity(stock)
        if (persisted_identity != expected_identity
                or persisted_source != self._validation_market_source(stock)):
            return False
        if (persisted_source == "INTEGRATED"
                and timeframe_key in _LIVE_MINUTE_TIMEFRAMES - {"M1"}
                and not self._direct_selected_query_valid(
                    direct_selected_query, stock, timeframe_key,
                    timeframe_minutes, persisted_identity, candles)):
            return False
        native_completed_receipt = None
        if persisted_source == "NXT":
            try:
                started, response = native_source_receipt
                if not _v5_native_receipt_is_current(stock, started, response, self._broker, self._now_factory()):
                    return False
                if ((response.get("timeframe_key") or f"M{response.get('interval')}") != timeframe_key):
                    return False
                native_completed_receipt = {
                    "stock_code": stock.code, "market_data_identity": persisted_identity,
                    "market_source": "NXT", "timeframe_key": timeframe_key, "completed_only": True,
                    "request_id": started["rqname"], "request_as_of": started["request_as_of"],
                    "received_at": response["received_at"], "connection_epoch": started["connection_epoch"],
                    "session_sha256": hashlib.sha256(started["login_session_id"].encode()).hexdigest(),
                    "source_content_sha256": hashlib.sha256(json.dumps(candles, ensure_ascii=False,
                        sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
                }
                if not self._nx_cached_rows_valid(stock, timeframe_minutes, timeframe_key, candles, native_completed_receipt):
                    return False
            except (ValueError, TypeError, KeyError, AttributeError):
                return False
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
                            **({"native_completed_receipt": native_completed_receipt}
                               if native_completed_receipt else {}),
                            **({"direct_m1_receipt": direct_m1_receipt}
                               if timeframe_key == "M1" and direct_m1_receipt else {}),
                            **({"direct_selected_query": direct_selected_query}
                               if direct_selected_query else {}),
                            **({"candle_content_sha256": hashlib.sha256(
                                json.dumps(
                                    candles,
                                    ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False,
                                ).encode("utf-8")
                            ).hexdigest()}),
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
        *, market_data_identity: str | None = None,
    ) -> Path | None:
        path_builder = getattr(self._historical_cache, "path_for", None)
        if not callable(path_builder):
            return None
        try:
            identity = (self._validation_market_data_identity(stock)
                        if market_data_identity is None else market_data_identity)
            if identity not in (stock.code, f"{stock.code}_AL", f"{stock.code}_NX"):
                return None
            source_kwargs = ({"market_data_identity": identity}
                             if identity.endswith(("_AL", "_NX")) else {})
            cache_path = Path(
                path_builder(
                    stock.code,
                    timeframe_minutes,
                    fetch_count,
                    timeframe_key,
                    **source_kwargs,
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
        source_verified_at: object = None,
        direct_selected_query: dict[str, object] | None = None,
        selected_direct_forming_snapshot: dict[str, object] | None = None,
    ) -> dict[str, object]:
        timeframe_key = session.request.timeframe_key
        pool_identity = str(market_data_identity or "").strip()
        pool_source = str(market_source or "").strip()
        expected_identity = self._validation_market_data_identity(session.request.stock)
        if (pool_identity != expected_identity
                or pool_source != self._validation_market_source(session.request.stock)):
            raise ValueError("MARKET_SOURCE_IDENTITY_INVALID")
        native_receipt = None
        native_forming = None
        native_cache_receipt = None
        if pool_source == "NXT" and request_id == "VALIDATION_CACHE_RESTORE":
            cached = self._cached_history(session.request.stock, session.request.timeframe_minutes,
                                          timeframe_key, requested_count)
            if cached is None or list(cached.candles) != candles:
                raise ValueError("NATIVE_CACHE_RECEIPT_UNAVAILABLE")
            marker = self._validation_cache_source_marker_path(session.request.stock,
                session.request.timeframe_minutes, timeframe_key, cached.requested_count)
            native_cache_receipt = json.loads(marker.read_text(encoding="utf-8"))["native_completed_receipt"]
        if (pool_source == "INTEGRATED" and timeframe_key in _LIVE_MINUTE_TIMEFRAMES - {"M1"}
                and request_id == "VALIDATION_CACHE_RESTORE"):
            marker_path = self._validation_cache_source_marker_path(
                session.request.stock, session.request.timeframe_minutes,
                timeframe_key, requested_count)
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            direct_selected_query = marker.get("direct_selected_query")
            if not self._direct_selected_query_valid(
                    direct_selected_query, session.request.stock, timeframe_key,
                    session.request.timeframe_minutes, pool_identity, candles):
                raise ValueError("SOR_DIRECT_CACHE_COMPLETION_UNVERIFIED")
        if pool_source in {"NXT", "KRX"}:
            native_receipt = self.__dict__.get("_native_minute_receipts", {}).get(request_id)
        if (pool_source == "NXT" and native_cache_receipt is None) or native_receipt is not None:
            if (native_receipt is None or native_receipt[0].get("rqname") != request_id
                    or (native_receipt[1].get("timeframe_key") or f"M{native_receipt[1].get('interval')}") != timeframe_key
                    or not _v5_native_receipt_is_current(session.request.stock, *native_receipt,
                            self._broker, self._now_factory(), live_session=pool_source == "NXT")):
                raise ValueError("NATIVE_RECEIPT_UNAVAILABLE")
            request_at = datetime.fromisoformat(native_receipt[0]["request_as_of"]).astimezone(SEOUL_TIMEZONE)
            plan = read_stock_market_source_plan(PROJECT_ROOT, session.request.stock.code, request_at.date())
            candles, _ = _v5_native_snapshot_parts(candles, plan, pool_source,
                                                   session.request.timeframe_minutes, request_at, timeframe_key)
            candidate = native_receipt[1].get("native_forming_candle")
            native_forming = dict(candidate) if isinstance(candidate, dict) else None
        verified_at = self._source_snapshot_datetime(source_verified_at)
        if verified_at is None and source_verified_at is None:
            try:
                verified_at = self._source_snapshot_datetime(self._now_factory())
            except Exception:
                verified_at = None
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
        al_display_history = ()
        if (pool_source == "NXT" and timeframe_key in _LIVE_MINUTE_TIMEFRAMES
                and timeframe_key != "M1"):
            # Freeze verified past AL data once per pool. Scrolling must not
            # re-read the cache, fetch AL in PRE, or change NX calculation/cache.
            entry = self._cached_history_for_source(
                session.request.stock, session.request.timeframe_minutes,
                timeframe_key, min(5000, requested_count),
                    f"{session.request.stock.code}_AL", "INTEGRATED",
                    legacy_display_only=True,
            )
            if entry is None:
                entry = self._cached_history_for_source(
                    session.request.stock, session.request.timeframe_minutes,
                    timeframe_key, 1, f"{session.request.stock.code}_AL", "INTEGRATED",
                    legacy_display_only=True,
                )
            if entry is not None:
                today = self._now_factory().astimezone(SEOUL_TIMEZONE).strftime("%Y%m%d")
                al_display_history = tuple(dict(row) for row in entry.candles
                    if row["time"][:8] < today and self._integrated_display_time_supported(
                        row, session.request.timeframe_minutes))
        return {
            "stock": session.request.stock,
            **({"nxt_al_display_history": al_display_history} if al_display_history else {}),
            **({"native_minute_receipt": native_receipt, "native_forming_candle": native_forming}
               if native_receipt is not None else {}),
            **({"native_cache_receipt": native_cache_receipt,
                "native_cache_candles": tuple(deepcopy(candles))} if native_cache_receipt else {}),
            "market_data_identity": pool_identity,
            "market_source": pool_source,
            "source_verified_at": verified_at.isoformat() if verified_at else "",
            **({"direct_selected_query": deepcopy(direct_selected_query)}
               if direct_selected_query else {}),
            **({"selected_direct_forming_snapshot": deepcopy(selected_direct_forming_snapshot)}
               if selected_direct_forming_snapshot else {}),
            "timeframe_minutes": session.request.timeframe_minutes,
            "timeframe_key": timeframe_key,
            "requested_count": requested_count,
            "actual_candle_count": len(candles),
            # A broker/cache request envelope is not evidence that the
            # requested number of candles actually exists.
            "source_request_underfilled": len(candles) < requested_count,
            "request_id": request_id,
            "snapshot": snapshot,
            "candles": candles,
            "signal_entries_by_settings_hash": {},
            "signal_entries_signature_by_settings_hash": {},
        }

    def _verified_validation_premarket_candles(
        self,
        session: ValidationSession,
        sidecar: object,
    ) -> list[dict[str, object]]:
        """Admit only verified source NXT PRE into detached Validation input."""
        request = session.request
        if not isinstance(sidecar, dict) or not (
            sidecar.get("stock_code") == request.stock.code
            and sidecar.get("timeframe_key") == request.timeframe_key
            and sidecar.get("timeframe_minutes") == request.timeframe_minutes
            and sidecar.get("market_data_identity") == f"{request.stock.code}_AL"
            and sidecar.get("market_source") == "INTEGRATED"
            and sidecar.get("display_only") is True
            and sidecar.get("verified_nxt_eligible") is True
        ):
            return []
        candles = sidecar.get("candles")
        if not isinstance(candles, list):
            return []
        minutes = request.timeframe_minutes
        if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes <= 0:
            return []
        # The target-timeframe TR may contain a currently forming M1 price.
        # An already-finished target bucket is safe; an open M3+ bucket
        # requires verified completed-M1 components (not present in this
        # _AL response), and must remain display-only until such evidence
        # is available. Never infer completion from callback/refresh time.
        raw_as_of = sidecar.get("request_as_of")
        if isinstance(raw_as_of, datetime):
            source_as_of = raw_as_of
        elif isinstance(raw_as_of, str) and raw_as_of:
            try:
                source_as_of = datetime.fromisoformat(raw_as_of)
            except ValueError:
                return []
        else:
            return []
        current_as_of = self._source_snapshot_datetime(self._now_factory())
        if (source_as_of.tzinfo is None or source_as_of.utcoffset() is None
                or current_as_of is None):
            return []
        source_as_of = source_as_of.astimezone(SEOUL_TIMEZONE)
        if source_as_of > current_as_of:
            return []
        valid: dict[str, dict[str, object]] = {}
        for candle in candles:
            if not isinstance(candle, dict):
                return []
            stamp = str(candle.get("time") or "")
            if len(stamp) != 14 or not stamp.isdigit():
                return []
            clock = int(stamp[8:10]) * 60 + int(stamp[10:12])
            if not 480 <= clock < 530:
                continue
            if (clock - 480) % minutes or stamp[12:14] != "00":
                return []
            if stamp in valid:
                return []
            try:
                candle_start = datetime.strptime(
                    stamp, "%Y%m%d%H%M%S"
                ).replace(tzinfo=SEOUL_TIMEZONE)
            except ValueError:
                return []
            session_end = candle_start.replace(
                hour=8, minute=50, second=0, microsecond=0,
            )
            completed_at = min(
                candle_start + timedelta(minutes=minutes), session_end,
            )
            if source_as_of < completed_at:
                continue
            try:
                prices = tuple(float(candle[field]) for field in
                               ("open", "high", "low", "close"))
                volume = float(candle["volume"])
            except (TypeError, ValueError, KeyError, OverflowError):
                return []
            if (not all(math.isfinite(value) and value > 0 for value in prices)
                    or prices[1] < max(prices[0], prices[2], prices[3])
                    or prices[2] > min(prices[0], prices[1], prices[3])):
                return []
            if not math.isfinite(volume) or volume < 0:
                return []
            valid[stamp] = {
                field: candle[field] for field in
                ("time", "open", "high", "low", "close", "volume")
            }
        return [valid[stamp] for stamp in sorted(valid)]

    @staticmethod
    def _verified_validation_aftermarket_candles(
        session: ValidationSession,
        sidecar: object,
    ) -> list[dict[str, object]]:
        """Completed direct selected-minute _AL AFTER candles, Validation-only."""
        request = session.request
        minutes = request.timeframe_minutes
        if (
            minutes not in (1, 3, 5, 10, 15, 30, 60, 120, 240)
            or not isinstance(sidecar, dict)
            or sidecar.get("stock_code") != request.stock.code
            or sidecar.get("timeframe_key") != request.timeframe_key
            or sidecar.get("timeframe_minutes") != minutes
            or sidecar.get("market_data_identity") != f"{request.stock.code}_AL"
            or sidecar.get("market_source") != "INTEGRATED"
            or sidecar.get("display_only") is not True
            or sidecar.get("verified_nxt_eligible") is not True
        ):
            return []
        raw_candles = sidecar.get("candles")
        if not isinstance(raw_candles, list):
            return []
        raw_as_of = sidecar.get("request_as_of")
        if isinstance(raw_as_of, datetime):
            source_as_of = raw_as_of
        elif isinstance(raw_as_of, str) and raw_as_of:
            try:
                source_as_of = datetime.fromisoformat(raw_as_of)
            except ValueError:
                return []
        elif raw_as_of is None:
            source_as_of = None
        else:
            return []
        if source_as_of is not None:
            if source_as_of.tzinfo is None or source_as_of.utcoffset() is None:
                return []
            source_as_of = source_as_of.astimezone(SEOUL_TIMEZONE)
        today = datetime.now(SEOUL_TIMEZONE).date()
        valid: dict[str, dict[str, object]] = {}
        for candle in raw_candles:
            if not isinstance(candle, dict):
                return []
            stamp = str(candle.get("time") or "")
            if len(stamp) != 14 or not stamp.isdigit():
                return []
            try:
                start = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(
                    tzinfo=SEOUL_TIMEZONE
                )
            except ValueError:
                return []
            clock = start.hour * 60 + start.minute
            # M3's broker bucket 15:39 includes the 15:40 AFTER opening.
            first = 15 * 60 + (39 if minutes == 3 else 40)
            after_open = start.replace(hour=15, minute=40, second=0)
            after_close = start.replace(hour=20, minute=0, second=0)
            # M120 19:00 and M240 17:00 close at 20:00; the direct
            # broker candle is a shorter final AFTER session bucket.
            end = min(start + timedelta(minutes=minutes), after_close)
            if not (
                first <= clock < 20 * 60
                and (clock - 9 * 60) % minutes == 0
                and start.second == 0
                and after_open < end <= after_close
            ):
                continue
            if stamp in valid:
                return []
            if source_as_of is not None:
                if source_as_of < end:
                    continue
            elif start.date() >= today:
                # Legacy sidecar without immutable request time is not proof
                # of a completed current-day bucket.
                continue
            try:
                prices = tuple(float(candle[k]) for k in (
                    "open", "high", "low", "close"
                ))
                volume = float(candle["volume"])
            except (KeyError, TypeError, ValueError, OverflowError):
                return []
            if (
                not all(math.isfinite(p) and p > 0 for p in prices)
                or not math.isfinite(volume) or volume < 0
                or prices[1] < max(prices[0], prices[2], prices[3])
                or prices[2] > min(prices[0], prices[1], prices[3])
            ):
                return []
            valid[stamp] = {
                k: candle[k]
                for k in ("time", "open", "high", "low", "close", "volume")
            }
        return [valid[stamp] for stamp in sorted(valid)]

    def _require_selected_timeframe_pool(
        self,
        session: ValidationSession,
        pool: object,
    ) -> None:
        """Never reinterpret source candles as another selected timeframe."""
        if not isinstance(pool, dict):
            raise ValueError("VALIDATION_POOL_UNAVAILABLE")
        if pool.get("stock") != session.request.stock:
            raise ValueError("VALIDATION_POOL_STOCK_MISMATCH")
        if (
            pool.get("timeframe_key") != session.request.timeframe_key
            or pool.get("timeframe_minutes") != session.request.timeframe_minutes
        ):
            raise ValueError("VALIDATION_POOL_TIMEFRAME_MISMATCH")
        source = pool.get("market_source")
        identity = str(pool.get("market_data_identity") or "")
        if (source != self._validation_market_source(session.request.stock)
                or identity != self._validation_market_data_identity(session.request.stock)):
            raise ValueError("VALIDATION_POOL_MARKET_IDENTITY_MISMATCH")
        native_live = pool.get("native_live_snapshot")
        if native_live is not None:
            if (not isinstance(native_live, dict)
                    or native_live.get("market_source") != source
                    or native_live.get("market_data_identity") != identity
                    or native_live.get("timeframe_minutes") != session.request.timeframe_minutes
                    or not _v5_host_native_proof_is_current(
                        session.request.stock, native_live, self._broker, self._now_factory())):
                raise ValueError("NATIVE_LIVE_RECEIPT_UNAVAILABLE")
            if pool.get("native_forming_included") is True and not _v5_host_native_forming_is_fresh(
                    native_live, self._now_factory()):
                raise ValueError("NATIVE_FORMING_RECEIPT_EXPIRED")
        if source == "NXT" and pool.get("native_cache_receipt") is not None:
            original = list(pool.get("native_cache_candles", ()))
            current = pool.get("candles")
            selected_times = {row.get("time") for row in current} if isinstance(current, list) else set()
            expected = [row for row in original if row["time"] in selected_times]
            if native_live is not None:
                direct = {row["time"]: row for row in pool.get("native_live_completed", ())}
                expected = [direct.get(row["time"], row) for row in expected]
                expected.extend(row for stamp, row in direct.items()
                                if stamp in selected_times and stamp not in {item["time"] for item in original})
                if pool.get("native_forming_included") is True:
                    expected.append(native_live["forming_candle"])
                expected.sort(key=lambda row: row["time"])
            if not self._nx_cached_rows_valid(session.request.stock, session.request.timeframe_minutes,
                    session.request.timeframe_key, original, pool["native_cache_receipt"]
                    ) or (current != expected and not (native_live is not None
                    and current == [row for row in original if row["time"] in selected_times])):
                raise ValueError("NATIVE_CACHE_RECEIPT_UNAVAILABLE")
        elif source == "NXT" or pool.get("native_minute_receipt") is not None:
            receipt = pool.get("native_minute_receipt")
            if (not isinstance(receipt, tuple) or len(receipt) != 2
                    or (receipt[1].get("timeframe_key") or f"M{receipt[1].get('interval')}") != session.request.timeframe_key
                    or not _v5_native_receipt_is_current(session.request.stock, *receipt,
                            self._broker, self._now_factory(), live_session=source == "NXT")):
                raise ValueError("NATIVE_RECEIPT_UNAVAILABLE")
            if pool.get("native_forming_included") is True and native_live is None:
                forming = pool.get("native_forming_candle")
                now = self._now_factory()
                if not _v5_native_forming_receipt_is_fresh(
                        session.request.stock, receipt, forming, self._broker, now):
                    raise ValueError("NATIVE_FORMING_RECEIPT_EXPIRED")

    @staticmethod
    def _integrated_regular_bucket_conflicts(
        calculation_candles: object,
        integrated_candles: object,
        timeframe_key: str,
    ) -> bool:
        """Keep M120/M240 indicators on their own direct-source candle axis."""
        if (
            timeframe_key not in {"M120", "M240"}
            or not isinstance(calculation_candles, list)
            or not isinstance(integrated_candles, list)
        ):
            return False
        regular_candles = {
            stamp: candle
            for candle in calculation_candles
            if isinstance(candle, dict)
            if len(stamp := str(candle.get("time") or "")) == 14
            and "0900" <= stamp[8:12] < "1530"
        }
        regular_days = {stamp[:8] for stamp in regular_candles}
        for candle in integrated_candles:
            if not isinstance(candle, dict):
                continue
            stamp = str(candle.get("time") or "")
            if not (
                len(stamp) == 14
                and stamp[:8] in regular_days
                and "0900" <= stamp[8:12] < "1530"
            ):
                continue
            matching = regular_candles.get(stamp)
            if matching is None or any(
                candle.get(field) != matching.get(field)
                for field in ("open", "high", "low", "close", "volume")
            ):
                return True
        return False

    def _validation_pool_for_session(
        self,
        session: ValidationSession,
        pool: dict[str, object],
        *,
        target_count: int | None = None,
        as_of: datetime | None = None,
        sidecar_override: object = _SIDECAR_UNSET,
    ) -> dict[str, object]:
        if not isinstance(pool, dict):
            return pool
        self._require_selected_timeframe_pool(session, pool)
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
        native_live = pool.get("native_live_snapshot")
        native_receipt = pool.get("native_minute_receipt")
        if native_live is not None:
            direct = {row["time"]: dict(row) for row in pool.get("native_live_completed", ())}
            merged = {row["time"]: row for row in normalized_raw
                      if row.get("is_complete") is not False}
            merged.update(direct)
            normalized_raw = [merged[stamp] for stamp in sorted(merged)]
            fresh = _v5_host_native_forming_is_fresh(native_live, self._now_factory())
            forming = native_live.get("forming_candle")
            if fresh:
                normalized_raw.append(dict(forming))
            pool = dict(pool, candles=normalized_raw, native_forming_included=fresh,
                        production_calculation_cache_identity=(
                            "NATIVE_SELECTED", native_live["market_data_identity"],
                            native_live["request_id"], native_live["session_identity"],
                            native_live["request_receipt"][1]["received_at"],
                            tuple(forming[field] for field in (
                                "time", "open", "high", "low", "close", "volume")),
                        ) if fresh else None)
        elif native_receipt is not None:
            normalized_raw = [row for row in normalized_raw if row.get("is_complete") is not False]
            forming = pool.get("native_forming_candle")
            now = self._now_factory()
            fresh = False
            if isinstance(forming, dict) and forming.get("is_complete") is False:
                fresh = _v5_native_forming_receipt_is_fresh(
                    session.request.stock, native_receipt, forming, self._broker, now)
            # The completed pool never receives this row, even after its deadline.
            if fresh:
                normalized_raw.append(dict(forming))
            pool = dict(pool, candles=normalized_raw, native_forming_included=fresh,
                        production_calculation_cache_identity=(
                            "NATIVE_FORMING", pool["market_data_identity"],
                            native_receipt[0]["rqname"], native_receipt[0]["connection_epoch"],
                            native_receipt[0]["login_session_id"], native_receipt[0]["request_as_of"],
                            native_receipt[1]["received_at"],
                            tuple(forming[key] for key in ("time", "open", "high", "low", "close", "volume")),
                        ) if fresh else None,
                        snapshot=ValidationHistoricalSnapshot(
                            stock=session.request.stock,
                            timeframe_minutes=session.request.timeframe_minutes,
                            timeframe_key=session.request.timeframe_key,
                            requested_count=max(1, len(normalized_raw)),
                            request_id=str(pool.get("request_id") or "POOL"),
                            rows=self._historical_rows_from_candles(normalized_raw),
                            market_data_identity=str(pool["market_data_identity"]),
                            market_source=str(pool["market_source"]),
                            request_as_of=native_receipt[0]["request_as_of"],
                        ))
        direct_m5_special = (
            session.request.timeframe_key == "M5"
            and pool.get("market_source") == "INTEGRATED"
            and pool.get("market_data_identity") == f"{session.request.stock.code}_AL"
            and any(str(row.get("time") or "").endswith("153500") for row in normalized_raw)
        )
        if direct_m5_special:
            stamps = [str(row.get("time") or "") for row in normalized_raw]
            special = [row for row in normalized_raw if str(row.get("time") or "").endswith("153500")]
            if (stamps != sorted(set(stamps))
                    or any(not isinstance(row.get("time"), str)
                           or len(row["time"]) != 14 or not row["time"].isdigit()
                           for row in special)
                    or len({row["time"][:8] for row in special}) != len(special)
                    or any(row.get("is_complete") is False
                           or row.get("display_only_special") is not None
                           or row.get("market_data_identity", f"{session.request.stock.code}_AL")
                              != f"{session.request.stock.code}_AL"
                           or row.get("market_source", "INTEGRATED") != "INTEGRATED"
                           or row.get("timeframe_minutes", 5) != 5
                           or any(type(row.get(field)) not in (int, float)
                                  or not math.isfinite(row[field]) for field in
                                  ("open", "high", "low", "close", "volume"))
                           or row["low"] <= 0 or row["volume"] < 0
                           or not row["low"] <= row["open"] <= row["high"]
                           or not row["low"] <= row["close"] <= row["high"]
                           for row in special)
                    or not self._direct_selected_query_valid(
                        pool.get("direct_selected_query"), session.request.stock,
                        "M5", 5, f"{session.request.stock.code}_AL", normalized_raw)):
                raise ValueError("SPECIAL_BAR_PROVENANCE_INVALID")
        calculation_raw, display_raw = split_native_closing_price_candles(
            [row for row in normalized_raw if not (direct_m5_special and
             str(row.get("time") or "").endswith("153500"))],
            timeframe_minutes=session.request.timeframe_minutes,
            market_data_identity=str(pool.get("market_data_identity") or ""),
            market_source=str(pool.get("market_source") or ""),
        )
        if direct_m5_special:
            display_raw = [dict(row, display_only_special=NATIVE_CLOSING_PRICE_SPECIAL)
                           if row in special else dict(row) for row in normalized_raw]
        special_present = len(calculation_raw) != len(display_raw)
        sidecar = (
            self._integrated_display_sidecars.get(
                self._integrated_display_sidecar_key(session)
            ) if sidecar_override is _SIDECAR_UNSET else sidecar_override
        )
        premarket = (
            self._verified_validation_premarket_candles(session, sidecar)
            if timeframe["kind"] == "MINUTE" and pool.get("market_source") == "INTEGRATED" else []
        )
        obsolete_projection_keys = (
            "display_candles", "display_only_integrated",
            "validation_composite_calculation", "validation_premarket_calculation",
            "validation_aftermarket_calculation", "validation_composite_content_hash",
            "validation_premarket_content_hash",
        )
        if (
            not regular_minute
            and len(normalized_raw) <= target_count
            and not any(key in pool for key in obsolete_projection_keys)
        ):
            if special_present:
                selected = dict(pool, candles=calculation_raw,
                                display_candles=display_raw,
                                display_only_integrated=True,
                                requested_count=len(calculation_raw))
                selected["snapshot"] = ValidationHistoricalSnapshot(
                    stock=session.request.stock,
                    timeframe_minutes=session.request.timeframe_minutes,
                    timeframe_key=session.request.timeframe_key,
                    requested_count=max(1, len(calculation_raw)),
                    request_id=str(pool.get("request_id") or "POOL"),
                    rows=self._historical_rows_from_candles(calculation_raw),
                    market_data_identity=str(pool.get("market_data_identity") or ""),
                    market_source=str(pool.get("market_source") or ""),
                )
                return self._compose_retained_native_nx_display(
                    session, self._compose_verified_nxt_pre(
                        session, selected, premarket, sidecar, normalized_raw,
                        display_target_count=target_count), target_count)
            return self._compose_retained_native_nx_display(
                session, self._compose_verified_nxt_pre(
                    session, pool, premarket, sidecar, normalized_raw,
                    display_target_count=target_count), target_count)
        target_minutes = session.request.timeframe_minutes
        target_key = session.request.timeframe_key
        projected = calculation_raw[-target_count:]
        derived = dict(pool)
        derived["timeframe_minutes"] = target_minutes
        derived["timeframe_key"] = target_key
        derived["requested_count"] = min(target_count, len(projected))
        derived["candles"] = projected
        for key in obsolete_projection_keys:
            derived.pop(key, None)
        derived["display_candles"] = (
            _krx_regular_session_candles(projected) if regular_minute
            else display_raw[-target_count:]
        )
        if (special_present and not regular_minute
                and (not direct_m5_special or any(
                    row.get("display_only_special") for row in derived["display_candles"]))):
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
            market_data_identity=str(derived.get("market_data_identity") or ""),
            market_source=str(derived.get("market_source") or ""),
        )
        return self._compose_retained_native_nx_display(
            session, self._compose_verified_nxt_pre(
                session, derived, premarket, sidecar, normalized_raw,
                display_target_count=target_count), target_count)

    def _nxt_pre_sidecar_identity(self, sidecar: object) -> tuple[object, ...]:
        if not isinstance(sidecar, dict):
            return ()
        try:
            content = self._marking_revision_candle_identity(sidecar.get("candles"))
        except (TypeError, ValueError, OverflowError):
            return ()
        return (
            sidecar.get("stock_code"), sidecar.get("timeframe_key"),
            sidecar.get("timeframe_minutes"), sidecar.get("market_data_identity"),
            sidecar.get("market_source"), sidecar.get("display_only"),
            sidecar.get("verified_nxt_eligible"), sidecar.get("request_as_of"),
            content,
        )

    def _retained_native_nx_pre_display(
        self, session: ValidationSession, old_pool: dict[str, object],
    ) -> tuple[dict[str, object], ...]:
        """Verify past NX source evidence before detaching display-only rows."""
        try:
            stock = session.request.stock
            key = session.request.timeframe_key
            minutes = session.request.timeframe_minutes
            now = self._now_factory().astimezone(SEOUL_TIMEZONE)
            plan = read_stock_market_source_plan(PROJECT_ROOT, stock.code, now.date())
            selected = source_for_time(plan, now) if plan else None
            rows = old_pool.get("candles")
            snapshot = old_pool.get("snapshot")
            current_broker = self._broker.broker_session_snapshot()
            if (key not in _LIVE_MINUTE_TIMEFRAMES
                    or plan is None or plan["stock_name"] != stock.name
                    or plan["nxt_available"] is not True
                    or selected is None or selected["source"] != "SOR"
                    or old_pool.get("stock") != stock
                    or old_pool.get("timeframe_key") != key
                    or old_pool.get("timeframe_minutes") != minutes
                    or old_pool.get("market_source") != "NXT"
                    or old_pool.get("market_data_identity") != f"{stock.code}_NX"
                    or not isinstance(rows, list) or not rows
                    or not isinstance(snapshot, ValidationHistoricalSnapshot)
                    or snapshot.stock != stock or snapshot.timeframe_key != key
                    or snapshot.timeframe_minutes != minutes
                    or snapshot.market_source != "NXT"
                    or snapshot.market_data_identity != f"{stock.code}_NX"
                    or snapshot.to_rows() != self._historical_rows_from_candles(rows)
                    or current_broker.connected is not True):
                return ()
            receipt = old_pool.get("native_minute_receipt")
            cached = old_pool.get("native_cache_receipt")
            if isinstance(receipt, tuple) and len(receipt) == 2:
                started, response = receipt
                requested = datetime.fromisoformat(started["request_as_of"])
                received = datetime.fromisoformat(response["received_at"])
                frozen = json.loads(self._native_minute_completed_content.get(
                    started["rqname"], "null"))
                original = {row["time"]: row for row in frozen["completed"]} if isinstance(
                    frozen, dict) else {}
                if (snapshot.request_id != started["rqname"]
                        or (response.get("timeframe_key") or f"M{response.get('interval')}") != key
                        or frozen.get("started") != started
                        or frozen.get("metadata") != response
                        or any(original.get(row["time"]) != row for row in rows)
                        or not _v5_native_receipt_is_current(
                            stock, started, response, self._broker, now, live_session=False)):
                    return ()
            elif isinstance(cached, dict):
                requested = datetime.fromisoformat(cached["request_as_of"])
                received = datetime.fromisoformat(cached["received_at"])
                original = list(old_pool.get("native_cache_candles", ()))
                selected_times = {row["time"] for row in rows}
                if (rows != [row for row in original if row["time"] in selected_times]
                        or not self._nx_cached_rows_valid(
                            stock, minutes, key, original, cached, retained_at=received)):
                    return ()
            else:
                return ()
            original_plan = read_stock_market_source_plan(
                PROJECT_ROOT, stock.code, requested.astimezone(SEOUL_TIMEZONE).date())
            if (original_plan is None
                    or (isinstance(receipt, tuple) and
                        requested.astimezone(SEOUL_TIMEZONE).date() != now.date())
                    or any(source_for_time(original_plan, at)["source"] != "NXT"
                           for at in (requested, received))):
                return ()
            completed, forming = _v5_native_snapshot_parts(
                rows, original_plan, "NXT", minutes, requested, key)
            if forming is not None or completed != rows:
                return ()
            verified = {row["time"]: dict(row, market_source="NXT",
                                          market_data_identity=f"{stock.code}_NX",
                                          is_complete=True) for row in rows
                        if "0800" <= row["time"][8:12] < "0850"}
            batches = old_pool.get("native_live_verified_batches")
            if not isinstance(batches, tuple):
                batches = ()
            if not batches and isinstance(old_pool.get("native_live_snapshot"), dict):
                batches = (old_pool["native_live_snapshot"],)
            for frozen_live in batches:
                try:
                    live = (json.loads(frozen_live) if isinstance(frozen_live, str)
                            else frozen_live)
                    if isinstance(frozen_live, str):
                        live["session_identity"] = tuple(live["session_identity"])
                    live_started, live_response = live["request_receipt"]
                    live_at = datetime.fromisoformat(live_response["received_at"])
                    live_rows = live.get("completed_candles")
                    live_requested = datetime.fromisoformat(live_started["request_as_of"])
                    source_rows, _ = _v5_native_snapshot_parts(
                        normalize_opt10080_rows(live_response["rows"]),
                        plan, "NXT", minutes, live_requested, key)
                    prices = {row["time"]: row for row in source_rows}
                    delta = {row["time"]: row for row in old_pool.get("native_live_completed", ())}
                    if (live_at.astimezone(SEOUL_TIMEZONE).date() == now.date()
                            and _v5_host_native_proof_is_current(
                                stock, live, self._broker, live_at)
                            and isinstance(live_rows, list)
                            and all(delta.get(row["time"]) == row for row in live_rows)
                            and all(prices.get(row["time"]) == {
                                field: row[field] for field in
                                ("time", "open", "high", "low", "close", "volume")}
                                for row in live_rows)):
                        for row in live_rows:
                            if (row.get("is_complete") is True
                                    and row.get("market_source") == "NXT"
                                    and row.get("market_data_identity") == f"{stock.code}_NX"
                                    and "0800" <= row["time"][8:12] < "0850"):
                                verified[row["time"]] = dict(row)
                except (AttributeError, KeyError, TypeError, ValueError):
                    pass  # An optional delta cannot invalidate the historical receipt.
            return tuple(verified[stamp] for stamp in sorted(verified))
        except (AttributeError, KeyError, TypeError, ValueError, OSError):
            return ()

    def _compose_retained_native_nx_display(
        self, session: ValidationSession, view: dict[str, object],
        display_target_count: int | None,
    ) -> dict[str, object]:
        evidence = view.get("_retained_native_nx_display")
        if not isinstance(evidence, dict) or not evidence.get("rows"):
            return view
        try:
            content = json.dumps(evidence["rows"], ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"), allow_nan=False).encode("utf-8")
            if hashlib.sha256(content).hexdigest() != evidence.get("content_sha256"):
                return view
        except (TypeError, ValueError):
            return view
        scope = session.request.settings_snapshot.to_dict().get("validation_market_scope")
        if isinstance(scope, dict) and scope.get("regular_market_only") is True:
            return view
        if (evidence.get("stock") != session.request.stock
                or evidence.get("session_id") != id(session)
                or evidence.get("timeframe_key") != session.request.timeframe_key
                or evidence.get("timeframe_minutes") != session.request.timeframe_minutes
                or view.get("market_source") not in {"INTEGRATED", "VALIDATION_COMPOSITE"}):
            return view
        display = {row["time"]: dict(row) for row in
                   view.get("display_candles", view["candles"])}
        for row in evidence["rows"]:
            if "0800" <= row["time"][8:12] < "0850":
                display[row["time"]] = dict(row)
        cap = min(5000, display_target_count or self._historical_count)
        return dict(view, display_candles=[display[stamp] for stamp in sorted(display)][-cap:],
                    display_only_integrated=True)

    def _retained_native_nx_publication_current(
        self, window: object, session: ValidationSession, pool: dict[str, object],
        generation: int, *, current_pool: dict[str, object] | None = None,
    ) -> bool:
        """Check the detached display evidence on the GUI thread before publication."""
        evidence = pool.get("_retained_native_nx_display")
        if evidence is None:
            return True
        if not isinstance(evidence, dict):
            return False
        key = id(window)
        now = self._now_factory().astimezone(SEOUL_TIMEZONE)
        broker = self._broker.broker_session_snapshot()
        plan = read_stock_market_source_plan(PROJECT_ROOT, session.request.stock.code, now.date())
        selected = source_for_time(plan, now) if plan else None
        try:
            content = json.dumps(evidence["rows"], ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"), allow_nan=False).encode("utf-8")
            return (self._open_windows.get(key) is window
                    and self._validation_sessions.get(key) is session
                    and self._historical_pools.get(key) is (
                        pool if current_pool is None else current_pool)
                    and self._request_generation.get(key) == generation
                    and evidence.get("window_key") == key
                    and evidence.get("session_id") == id(session)
                    and evidence.get("generation") == generation
                    and evidence.get("stock") == session.request.stock
                    and evidence.get("timeframe_key") == session.request.timeframe_key
                    and evidence.get("timeframe_minutes") == session.request.timeframe_minutes
                    and evidence.get("trading_day") == now.date().isoformat()
                    and evidence.get("broker_identity") == (
                        broker.connection_epoch, broker.login_session_id)
                    and broker.connected is True
                    and plan is not None and plan["stock_name"] == session.request.stock.name
                    and plan["nxt_available"] is True
                    and selected is not None and selected["source"] == "SOR"
                    and hashlib.sha256(content).hexdigest() == evidence.get("content_sha256"))
        except (KeyError, TypeError, ValueError, OSError):
            return False

    def _compose_verified_nxt_pre(
        self, session: ValidationSession, sor_view: dict[str, object],
        premarket: list[dict[str, object]],
        sidecar: object = None,
        all_sor: object = None,
        *, display_target_count: int | None = None,
    ) -> dict[str, object]:
        """Keep the AL view intact and add only absent proven PRE timestamps."""
        if sor_view.get("market_source") == "NXT" and sor_view.get("nxt_al_display_history"):
            # This is a display axis only. NX rows (including a fresh forming
            # row) win at shared timestamps; AL cannot enter replay or signals.
            display = {row["time"]: dict(row) for row in sor_view["nxt_al_display_history"]}
            display.update({row["time"]: dict(row)
                            for row in sor_view.get("display_candles", sor_view["candles"])})
            rows = [display[stamp] for stamp in sorted(display)]
            scope = session.request.settings_snapshot.to_dict().get("validation_market_scope")
            if isinstance(scope, dict) and scope.get("regular_market_only") is True:
                rows = _krx_regular_session_candles(rows)
            return dict(sor_view, display_candles=rows[-(display_target_count or self._historical_count):],
                        display_only_integrated=True)
        sor = sor_view.get("candles")
        if not premarket or not isinstance(sor, list) or not sor:
            return sor_view
        by_time = {str(row.get("time") or ""): row for row in sor}
        if len(by_time) != len(sor):
            return sor_view
        all_sor_times = (
            {str(row.get("time") or "") for row in all_sor
             if isinstance(row, dict)}
            if isinstance(all_sor, list) else set(by_time)
        )
        earliest_day = str(sor[0].get("time") or "")[:8]
        missing = [row for row in premarket
                   if row["time"] not in all_sor_times
                   and row["time"][:8] >= earliest_day]
        if not missing:
            return sor_view
        calculation = sorted([*sor, *missing], key=lambda row: row["time"])
        rules = session.request.settings_snapshot.to_dict()
        scope = rules.get("validation_market_scope")
        regular_only = isinstance(scope, dict) and scope.get("regular_market_only") is True
        display_source = sor_view.get("display_candles", sor)
        display = (_krx_regular_session_candles(calculation)
                   if regular_only else sorted([*display_source, *missing],
                                               key=lambda row: row["time"]))
        composite = dict(sor_view)
        composite.update(
            candles=calculation,
            display_candles=display,
            requested_count=len(calculation),
            market_source="VALIDATION_COMPOSITE",
            market_data_identity=f"{session.request.stock.code}_AL_NXT_PRE_VALIDATION",
            validation_composite_calculation=True,
            validation_premarket_calculation=True,
            validation_premarket_content_hash=self._marking_revision_candle_identity(missing),
            _validation_sor_view=sor_view,
            _validation_nxt_pre_sidecar_identity=self._nxt_pre_sidecar_identity(sidecar),
        )
        sor_snapshot = sor_view.get("snapshot")
        source_as_of = (
            getattr(sor_snapshot, "request_as_of", None)
            or sor_view.get("source_verified_at")
            or (sidecar.get("request_as_of") if isinstance(sidecar, dict) else None)
        )
        composite["snapshot"] = ValidationHistoricalSnapshot(
            stock=session.request.stock,
            timeframe_minutes=session.request.timeframe_minutes,
            timeframe_key=session.request.timeframe_key,
            requested_count=len(calculation),
            request_id=f"{sor_view.get('request_id') or 'POOL'}:NXT_PRE",
            rows=self._historical_rows_from_candles(calculation),
            market_data_identity=composite["market_data_identity"],
            market_source=composite["market_source"],
            request_as_of=str(source_as_of) if source_as_of is not None else None,
        )
        return composite

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
                IndicatorFollowSignalValidationV5Flow._pool_computation_source_identity(pool)
            )
        fields = ("candles", "display_candles", "production_session_contract",
                  "direct_selected_query", "selected_direct_forming_snapshot")
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
        window_key = id(window)
        binding = self._native_transition_nx_bindings.get(window_key)
        if pool.get("market_source") == "INTEGRATED" and isinstance(binding, tuple):
            old_window, old_session, old_generation = binding
            if (old_window is window and isinstance(old_session, ValidationSession)
                    and old_session.request.stock == session.request.stock
                    and old_session.request.timeframe_key == session.request.timeframe_key
                    and old_session.request.timeframe_minutes == session.request.timeframe_minutes
                    and self._request_generation.get(window_key, 0) == old_generation + 1):
                old_nx = self._native_transition_nx_pools.get(window_key)
                rows = (self._retained_native_nx_pre_display(session, old_nx)
                        if isinstance(old_nx, dict) else ())
                if rows:
                    content = json.dumps(rows, ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":"), allow_nan=False).encode("utf-8")
                    pool = dict(pool, _retained_native_nx_display={
                        "stock": session.request.stock,
                        "timeframe_key": session.request.timeframe_key,
                        "timeframe_minutes": session.request.timeframe_minutes,
                        "window_key": window_key,
                        "session_id": id(session),
                        "generation": self._request_generation.get(window_key, 0),
                        "trading_day": self._now_factory().astimezone(SEOUL_TIMEZONE).date().isoformat(),
                        "broker_identity": (
                            self._broker.broker_session_snapshot().connection_epoch,
                            self._broker.broker_session_snapshot().login_session_id),
                        "rows": rows,
                        "content_sha256": hashlib.sha256(content).hexdigest(),
                    })
        retained = pool.get("_retained_native_nx_display")
        if (retained is not None
                and (not isinstance(retained, dict) or not isinstance(binding, tuple)
                     or retained.get("window_key") != window_key
                     or retained.get("session_id") != id(session)
                     or retained.get("generation") != self._request_generation.get(window_key, 0)
                     or retained.get("trading_day") != self._now_factory().astimezone(
                         SEOUL_TIMEZONE).date().isoformat()
                     or retained.get("broker_identity") != (
                         self._broker.broker_session_snapshot().connection_epoch,
                         self._broker.broker_session_snapshot().login_session_id))):
            pool = dict(pool)
            pool.pop("_retained_native_nx_display", None)
        shared = dict(pool)
        shared.pop("_retained_native_nx_display", None)
        _SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS[
            self._shared_pool_key(session.request.stock,
                                  str(pool.get("timeframe_key") or ""))
        ] = shared
        old_pool = self._historical_pools.get(window_key)
        transition = self._native_source_transition.get(window_key)
        transition_used = False
        if (isinstance(transition, dict) and pool.get("market_source") == "INTEGRATED"
                and pool.get("market_data_identity") == transition.get("market_data_identity")):
            # Attach the host's still-current proof before the first AL prepare.
            # That prepare must use the native selected interval, not the old M1 gate.
            self._native_source_transition.pop(window_key, None)
            self._historical_pools[window_key] = pool
            self._validation_sessions[window_key] = session
            self._handle_native_selected_snapshot_accepted(transition)
            transition_used = (window_key in self._native_selected_pending
                               or self._historical_pools.get(window_key) is not pool)
        try:
            if not transition_used:
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
        _warmup_ready: bool = False,
    ) -> bool:
        if getattr(window, "_normal_base_requested", False) and window.replay_snapshot is None:
            if provider_request_key is not None and self._active_providers.get(provider_request_key) is provider:
                self._active_providers.pop(provider_request_key, None)
            return self._install_pool(
                window, session, pool, evaluation_count=evaluation_count,
                replacement_reason=replacement_reason)
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
        range_times = getattr(window, "_validation_range_times", None)
        manual_browse = bool(
            getattr(window, "_range_replay_entries_available", False)
            or (
                isinstance(window.replay_snapshot, ValidationReplaySnapshot)
                and isinstance(range_times, tuple)
                and len(range_times) == 2
                and all(str(value or "") for value in range_times)
            )
        )
        if manual_browse and not _warmup_ready:
            snapshot = window.replay_snapshot
            generation = self._request_generation.get(window_key, 0)
            history_target = self._history_targets.get(window_key, self._historical_count)

            def resume(enriched, view, deficit):
                if (self._request_generation.get(window_key, 0) != generation
                        or self._open_windows.get(window_key) is not window):
                    return
                if (window.replay_snapshot is not snapshot
                        or self._historical_pools.get(window_key) is not source_pool
                        or self._history_targets.get(window_key, self._historical_count) != history_target):
                    self._complete_history_extension(window_key, success=False)
                    return
                if deficit:
                    self._fail_window(window, f"HISTORY_BROWSE_WARMUP_INSUFFICIENT: missing={deficit}")
                    return
                self._use_pool_for_history_browse(
                    window, session, enriched, evaluation_count=evaluation_count,
                    prepared_view_pool=view, replacement_reason=replacement_reason,
                    _warmup_ready=True)

            if provider_request_key is not None and self._active_providers.get(provider_request_key) is provider:
                self._active_providers.pop(provider_request_key, None)
            if self._ensure_normal_base_history(
                    window, session, pool, evaluation_count,
                    display_view=prepared_view_pool, on_ready=resume):
                return True
        browse_ui_probe = _perf_phase(window) if _PERF_ENABLED else None
        try:
            captured = capture(session.request.settings_snapshot)
            if browse_ui_probe is not None:
                browse_ui_probe.checkpoint('browse.ui_capture')
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
            if browse_ui_probe is not None:
                browse_ui_probe.checkpoint(
                    'browse.ui_detach', history_target=int(history_target),
                    source_bars=len(worker_source_candles or ()),
                    pool_bars=len(worker_pool.get("candles") or ()),
                    entries=len(worker_signal_entries),
                )
        except Exception as exc:
            self._fail_window(window, f"HISTORY_BROWSE_PREPARE_ERROR: {exc}")
            return False
        token = object()
        generation = self._request_generation.get(window_key, 0)
        previous_indicator_cache = self._history_browse_indicator_cache.get(window_key)
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

        @_perf_stage("worker.prepare_browse", owner=window)
        def prepare_browse() -> None:
            payload = dict(payload_base)
            browse_probe = (_perf_phase(
                window, history_target=int(history_target),
                source_bars=len(worker_source_candles or ()),
                pool_bars=len(worker_pool.get("candles") or ()),
            ) if _PERF_ENABLED else None)
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
                    if browse_probe is not None:
                        browse_probe.checkpoint('browse.cache_lease')
                        source_cache_hit = worker_pool.get("_source_identity_content_cache") is not None
                    source_identity = self._pool_computation_source_identity(
                        worker_pool, _cache_flat_content=True,
                    )
                    if browse_probe is not None:
                        browse_probe.checkpoint(
                            'browse.source_identity', source_cache_hit=source_cache_hit,
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
                    if browse_probe is not None:
                        browse_probe.checkpoint('browse.dispatch_snapshot')
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
                    if manual_browse:
                        view_pool, deficit = self._normal_base_history_view(
                            session, worker_pool, history_target, view_pool)
                        if deficit:
                            raise ValueError(f"HISTORY_BROWSE_WARMUP_INSUFFICIENT: missing={deficit}")
                        # Retain the complete prior calculation prefix for range/marking identity.
                        view_pool["candles"] = worker_pool["candles"]
                    if self._verified_sor_tail_provider is not None:
                        now = self._now_factory()
                        now = (now.replace(tzinfo=SEOUL_TIMEZONE) if now.tzinfo is None
                               else now.astimezone(SEOUL_TIMEZONE))
                        view_pool = self._verified_sor_view_for_session(
                            session, worker_pool, view_pool, now, source_identity,
                        )
                    if browse_probe is not None:
                        browse_probe.checkpoint(
                            'browse.view_prepare', manual=bool(manual_browse),
                            view_reused=isinstance(worker_view_pool, dict),
                            calculation_bars=len(view_pool.get("candles") or ()),
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
                    if not coverage_ok and not manual_browse:
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
                    if browse_probe is not None:
                        browse_probe.checkpoint(
                            'browse.index_mapping',
                            display_bars=len(display_candles or ()),
                            mapped_bars=len(backed_indexes),
                            coverage_ok=bool(coverage_ok),
                        )
                    # Reuse the *full* series only for an identical detached
                    # source and identical signal/descriptor authority.
                    # The chart-specific index mapping is always rebuilt.
                    cache_eligible = (
                        (manual_browse and (
                            active_candles is worker_pool.get("candles")
                            or (self._verified_sor_tail_provider is not None
                                and "sor_tail_signature" in view_pool)
                        ))
                        or (
                            not manual_browse
                            and isinstance(active_candles, list)
                            and not display_only_integrated
                            and all(view_pool.get(field) == worker_pool.get(field)
                                    for field in ("market_data_identity", "market_source",
                                                  "timeframe_key", "timeframe_minutes"))
                        )
                    )
                    cache_key = (
                        source_identity,
                        session.request.settings_snapshot.rules_hash,
                        worker_visualizations,
                        tuple(worker_signal_entries),
                        *((view_pool["sor_tail_signature"],)
                          if "sor_tail_signature" in view_pool else ()),
                    )
                    cached_series = (
                        previous_indicator_cache[1]
                        if (
                            cache_eligible
                            and isinstance(previous_indicator_cache, tuple)
                            and len(previous_indicator_cache) in (2, 3)
                            and previous_indicator_cache[0] == cache_key
                        )
                        else None
                    )
                    if cache_eligible and cached_series is None and not manual_browse:
                        cached_series = _extend_prepend_indicator_cache(
                            previous_indicator_cache,
                            active_candles,
                            worker_source_candles,
                            cache_key,
                            session.request.settings_snapshot.to_dict(),
                            worker_visualizations,
                            cancel_event,
                        )
                    captured_series = []
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
                            precomputed_indicator_cache=cached_series,
                            capture_indicator_cache=(
                                captured_series.append if cache_eligible else None
                            ),
                        )
                    )
                    if cache_eligible and (cached_series is not None or captured_series):
                        payload["_accepted_indicator_cache"] = (
                            cache_key,
                            cached_series if cached_series is not None else captured_series[0],
                            tuple(deepcopy(active_candles)),
                        )
                    if browse_probe is not None:
                        browse_probe.checkpoint(
                            'browse.presentation_total',
                            full_series_cache_hit=cached_series is not None,
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
                                native_source_receipt=worker_pool.get("native_minute_receipt"),
                                direct_selected_query=worker_pool.get("direct_selected_query"),
                            )
                    if browse_probe is not None:
                        browse_probe.checkpoint(
                            'browse.persist_total',
                            persisted=payload.get("cache_persisted") is True,
                            cancelled=cancel_event.is_set(),
                        )
                    if cancel_event.is_set():
                        payload["cancelled"] = True
            except Exception as exc:
                payload["error"] = str(exc)
            if cancel_event.is_set():
                payload["cancelled"] = True
            if browse_probe is not None:
                payload["_perf_emitted_mono"] = time.perf_counter()
                browse_probe.checkpoint(
                    'browse.result_ready',
                    cancelled=payload.get("cancelled") is True,
                    failed=bool(payload.get("error")),
                )
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
        receive_probe = (_perf_phase(
            window, history_target=(payload.get("history_target")
                                    if type(payload.get("history_target")) is int else 0),
        ) if _PERF_ENABLED and window is not None else None)
        if receive_probe is not None:
            emitted_at = payload.get("_perf_emitted_mono")
            receive_probe.checkpoint(
                'browse.gui_receive',
                queue_delay_ms=round(max(0.0, (time.perf_counter() - emitted_at) * 1000), 3)
                if isinstance(emitted_at, (float, int)) else None,
                cancelled=payload.get("cancelled") is True,
            )
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
        if not self._retained_native_nx_publication_current(
                window, session, pool, generation, current_pool=source_pool):
            self._history_browse_active.pop(window_key, None)
            release_provider()
            self._complete_history_extension(window_key, success=False)
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
            if receive_probe is not None:
                receive_probe.checkpoint('browse.gui_apply')
        except Exception as exc:
            self._history_browse_active.pop(window_key, None)
            release_provider()
            self._fail_window(window, f"HISTORY_BROWSE_VIEW_ERROR: {exc}")
            return
        old_pool = self._historical_pools.get(window_key)
        self._historical_pools[window_key] = pool
        self._validation_sessions[window_key] = session
        shared = dict(pool)
        shared.pop("_retained_native_nx_display", None)
        _SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS[
            self._shared_pool_key(
                session.request.stock,
                str(pool.get("timeframe_key") or ""),
            )
        ] = shared
        replacement_reason = payload.get("replacement_reason")
        if replacement_reason is not None:
            self._trace_pool_replaced(
                reason=str(replacement_reason),
                window_key=window_key,
                old_pool=old_pool,
                new_pool=pool,
            )
        # Publish only accepted worker results; never share a stale candidate.
        accepted_indicator_cache = payload.get("_accepted_indicator_cache")
        if (
            isinstance(accepted_indicator_cache, tuple)
            and len(accepted_indicator_cache) == 3
        ):
            self._history_browse_indicator_cache[window_key] = accepted_indicator_cache
        else:
            self._history_browse_indicator_cache.pop(window_key, None)
        self._history_browse_active.pop(window_key, None)
        release_provider()
        self._complete_history_extension(window_key, success=True)
        self._ensure_integrated_display_sidecar(
            window,
            session,
            int(payload["history_target"]),
        )
        if receive_probe is not None:
            receive_probe.checkpoint('browse.gui_finalize')

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
        normal_base_presentation: object | None = None,
        m1_marking_basis: object = None,
    ) -> bool:
        window_key = id(window)
        publication_generation = self._request_generation.get(window_key, 0)
        chart_candle_count = self._history_targets.get(
            window_key,
            self._historical_count,
        )
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
        restore_premarket_anchor = None
        if (
            normal_base_presentation is not None
            and view_pool.get("validation_composite_calculation") is True
            and getattr(window, "_normal_base_ready", False)
            and getattr(window, "_normal_base_requested", False)
        ):
            capture_anchor = getattr(
                window, "_capture_history_extension_view_anchor", None,
            )
            if callable(capture_anchor):
                restore_premarket_anchor = capture_anchor()
        marking_revision_pending = bool(
            normal_base_presentation is not None
            and getattr(window, "_marking_times", None) is not None
            and getattr(window, "_marking_snapshot", None) is not None
            and window.marking_display_check.isChecked()
        )
        if marking_revision_pending:
            defer_revision = getattr(
                window,
                "defer_marking_source_revision",
                None,
            )
            marking_revision_pending = bool(
                callable(defer_revision)
                and defer_revision(
                    generation=getattr(window, "_marking_generation", None),
                    key=getattr(window, "_marking_key", None),
                    times=getattr(window, "_marking_times", None),
                    settings=getattr(window, "_marking_settings", None),
                )
            )

        def fail_pending_revision(error: object) -> None:
            if not marking_revision_pending:
                return
            fail = getattr(window, "fail_marking_source_revision", None)
            if callable(fail):
                fail(
                    generation=getattr(window, "_marking_generation", None),
                    previous_key=getattr(window, "_marking_key", None),
                    times=getattr(window, "_marking_times", None),
                    settings=getattr(window, "_marking_settings", None),
                    error=error,
                )

        apply_atomic_result = getattr(
            window,
            "apply_prepared_validation_result",
            None,
        )
        atomic_result_applied = False
        normal_base_reuse_canvas = False
        if normal_base_presentation is not None:
            display_candles = view_pool.get("display_candles")
            visible_display = (
                view_pool["candles"][-chart_candle_count:]
                if not isinstance(display_candles, list)
                else display_candles[-chart_candle_count:]
            )
            normal_base_reuse_canvas = bool(
                getattr(window, "canvas", None) is not None
                and session.request.timeframe_key
                == getattr(window, "_normal_base_timeframe_key", "")
                and view_pool["candles"]
                == getattr(window, "_calculation_candles", None)
                and visible_display == getattr(window, "_candles", None)
                and chart_candle_count
                == getattr(window, "_historical_candle_count", None)
                and bool(view_pool.get("display_only_integrated") is True)
                == bool(getattr(window, "_display_only_integrated_active", False))
            )
        try:
            if (
                replay_snapshot is not None
                and prepared_presentation is not None
                and callable(apply_atomic_result)
                and getattr(window, "canvas", None) is not None
            ):
                apply_atomic_result(
                    calculation_candles=view_pool["candles"],
                    chart_candle_count=chart_candle_count,
                    replay_snapshot=replay_snapshot,
                    prepared_presentation=prepared_presentation,
                    display_candles=view_pool.get("display_candles"),
                    display_only_integrated=(
                        view_pool.get("display_only_integrated") is True
                    ),
                )
                atomic_result_applied = True
            else:
                if not normal_base_reuse_canvas:
                    set_historical_candle_count = getattr(
                        window,
                        "set_historical_candle_count",
                        None,
                    )
                    if callable(set_historical_candle_count):
                        set_historical_candle_count(chart_candle_count)
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
                stage_signal_entries = getattr(
                    window,
                    "stage_signal_marker_entries",
                    None,
                )
                set_signal_entries = getattr(
                    window,
                    "set_signal_marker_entries",
                    None,
                )
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
        except Exception as exc:
            fail_pending_revision(exc)
            raise
        if normal_base_presentation is not None:
            if restore_premarket_anchor is not None:
                window._pending_history_extension_view_anchor = (
                    restore_premarket_anchor
                )
            try:
                window.install_normal_base(
                    session.request.settings_snapshot,
                    session.request.timeframe_key,
                    presentation=normal_base_presentation,
                    defer_marking_source_revision=marking_revision_pending,
                    reuse_canvas=normal_base_reuse_canvas,
                )
                if marking_revision_pending:
                    started = self._start_marking_source_revision(
                        window,
                        session,
                        candidate_view_pool=view_pool,
                        normal_base_presentation=normal_base_presentation,
                    )
                    if (
                        not started
                        and getattr(window, "_marking_times", None) is not None
                    ):
                        fail_pending_revision(
                            "MARKING_SOURCE_REVISION_START_FAILED"
                        )
            except Exception as exc:
                fail_pending_revision(exc)
                raise
        elif replay_snapshot is not None:
            if not atomic_result_applied:
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
        try:
            self._remember_published_direct_current(
                window, session, pool, view_pool, publication_generation)
        except Exception:
            pass  # Replay basis is optional; a failed binding cannot undo the chart.
        try:
            self._remember_published_m1_marking_basis(
                window, session, pool, view_pool, publication_generation,
                m1_marking_basis)
        except Exception:
            window._v5_m1_marking_basis = None
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

    def _trading_cost_reuse_prefix(
        self,
        session: ValidationSession,
        pool: object,
    ) -> tuple[object, ...] | None:
        if self._replay_factory is not ValidationHistoricalReplay or not isinstance(pool, dict):
            return None
        if (
            pool.get("display_only_integrated") is True
            or "COMPOSITE" in str(pool.get("market_source") or "").upper()
            or pool.get("source_coverage_sufficient") is False
        ):
            return None
        rules = session.request.settings_snapshot.to_dict()
        execution = rules.get("validation_execution")
        if not isinstance(execution, dict) or not {
            "trading_cost_enabled",
            "trading_cost_percent",
        }.issubset(execution):
            return None
        neutral_rules = deepcopy(rules)
        neutral_execution = neutral_rules["validation_execution"]
        neutral_execution.pop("trading_cost_enabled", None)
        neutral_execution.pop("trading_cost_percent", None)
        ui_state = neutral_rules.get("indicator_follow_ui_state")
        ui_execution = (
            ui_state.get("state", {}).get("validation_execution")
            if isinstance(ui_state, dict)
            and isinstance(ui_state.get("state"), dict)
            else None
        )
        if isinstance(ui_execution, dict):
            ui_execution.pop("trading_cost_enabled", None)
            ui_execution.pop("trading_cost_percent", None)
        try:
            projection_identity = json.dumps(
                [
                    pool.get("production_calculation_cache_identity"),
                    pool.get("forming_bar_status"),
                ],
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            neutral_hash = hashlib.sha256(json.dumps(
                neutral_rules,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
        except (TypeError, ValueError, OverflowError):
            return None
        return (
            "TRADING_COST_ONLY_REPLAY_V1",
            neutral_hash,
            session.request.stock,
            session.request.timeframe_minutes,
            session.request.timeframe_key,
            str(pool.get("timeframe_key") or ""),
            str(pool.get("market_data_identity") or ""),
            str(pool.get("market_source") or ""),
            projection_identity,
            str(pool.get("request_id") or ""),
        )

    def _trading_cost_replay_key(
        self,
        session: ValidationSession,
        pool: object,
        candles: list[dict[str, object]],
        evaluated_start_index: int,
        evaluated_end_index: int,
    ) -> tuple[object, ...] | None:
        prefix = self._trading_cost_reuse_prefix(session, pool)
        if prefix is None:
            return None
        try:
            replay_values = [
                (
                    str(candle.get("time") or ""),
                    float(candle.get("open")),
                    float(candle.get("high")),
                    float(candle.get("low")),
                    float(candle.get("close")),
                    float(candle.get("volume")),
                )
                for candle in candles
            ]
            candle_hash = hashlib.sha256(json.dumps(
                replay_values,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
        except (TypeError, ValueError, OverflowError):
            return None
        return prefix + (
            int(evaluated_start_index),
            int(evaluated_end_index),
            len(candles),
            candle_hash,
        )

    @classmethod
    def _rebind_trading_cost_replay_snapshot(
        cls,
        snapshot: ValidationReplaySnapshot,
        settings_hash: str,
    ) -> ValidationReplaySnapshot:
        return ValidationReplaySnapshot(
            stock=snapshot.stock,
            timeframe_minutes=snapshot.timeframe_minutes,
            timeframe_key=snapshot.timeframe_key,
            settings_hash=settings_hash,
            historical_request_id=snapshot.historical_request_id,
            evaluated_start_index=snapshot.evaluated_start_index,
            evaluated_end_index=snapshot.evaluated_end_index,
            dropped_raw_rows_count=snapshot.dropped_raw_rows_count,
            candles=snapshot.to_candles(),
            entries=snapshot.to_entries(),
        )

    @classmethod
    def _publish_trading_cost_replay(
        cls,
        pool: dict[str, object],
        key: object,
        snapshot: object,
    ) -> None:
        if (
            not isinstance(key, tuple)
            or len(key) < 2
            or not isinstance(snapshot, ValidationReplaySnapshot)
        ):
            return
        cached = pool.get("trading_cost_replay_cache")
        retained = (
            {
                cached_key: cached_snapshot
                for cached_key, cached_snapshot in cached.items()
                if (
                    isinstance(cached_key, tuple)
                    and len(cached_key) >= 2
                    and cached_key[1] == key[1]
                    and isinstance(cached_snapshot, ValidationReplaySnapshot)
                )
            }
            if isinstance(cached, dict)
            else {}
        )
        retained[key] = cls._detached_detail_replay_snapshot(snapshot)
        while len(retained) > 4:
            retained.pop(next(iter(retained)))
        pool["trading_cost_replay_cache"] = retained

    @_perf_stage("worker.validation")
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
        reusable_cost_replays: dict[object, object] | None = None,
        verified_source_identity: tuple[object, ...] | None = None,
        sor_m1_calculation_snapshot: object = None,
        expected_sor_m1_calculation: object = None,
        marking_view_check: Callable[[dict[str, object]], dict[str, object]] | None = None,
    ) -> ValidationReplayResult:
        if not isinstance(pool, dict):
            raise ValueError("HISTORICAL_POOL_UNAVAILABLE")
        rules = session.request.settings_snapshot.to_dict()
        warmup_bars = required_validation_history_context_bars(rules)
        active_pool = prepared_view_pool
        if active_pool is None and chart_range is not None:
            active_pool = self._range_replay_history_view(session, pool, history_target)
        if active_pool is None:
            active_pool = self._validation_pool_for_session(
                session,
                pool,
                target_count=history_target + warmup_bars,
            )
        if (chart_range is not None
                and session.request.stock.code == "005930"
                and session.request.timeframe_key == "M3"
                and isinstance(sor_m1_calculation_snapshot, tuple)
                and len(sor_m1_calculation_snapshot) == 3
                and isinstance(sor_m1_calculation_snapshot[2], dict)
                and sor_m1_calculation_snapshot[2].get("projection")
                   == "DIRECT_COMPLETED_TIMEFRAME"
                and isinstance(expected_sor_m1_calculation, list)
                and isinstance(active_pool, dict)
                and isinstance(active_pool.get("candles"), list)
                and len(expected_sor_m1_calculation) > len(active_pool["candles"]) + 1):
            # Rebuild the displayed prefix from the selected source, then compare every row.
            active_pool = self._validation_pool_for_session(
                session, pool, target_count=len(expected_sor_m1_calculation) - 1)
        active_pool = self._sor_m1_calculation_view(
            session, pool, active_pool, sor_m1_calculation_snapshot,
            expected_sor_m1_calculation,
        )
        if self._verified_sor_tail_provider is not None and active_pool.get("market_source") not in {"NXT", "KRX"}:
            if verified_source_identity is not None:
                now = self._now_factory()
                now = (now.replace(tzinfo=SEOUL_TIMEZONE) if now.tzinfo is None
                       else now.astimezone(SEOUL_TIMEZONE))
                active_pool = self._verified_sor_view_for_session(
                    session, pool, active_pool, now, verified_source_identity,
                )
            if not active_pool.get("sor_tail_calculation_complete"):
                raise ValueError("SOR_VERIFIED_BLOCKED:REPLAY_VIEW_UNVERIFIED")
        if marking_view_check is not None:
            active_pool = marking_view_check(active_pool)
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

        cached_start = (
            len(replay_candles) - count
            if replay_start_index is None
            else replay_start_index
        )
        cached_end = (
            len(replay_candles) - 1
            if replay_end_index is None
            else replay_end_index
        )
        cost_replay_key = self._trading_cost_replay_key(
            session,
            active_pool,
            replay_candles,
            cached_start,
            cached_end,
        )
        cached_cost_replay = (
            reusable_cost_replays.get(cost_replay_key)
            if cost_replay_key is not None and isinstance(reusable_cost_replays, dict)
            else None
        )
        if (
            isinstance(cached_cost_replay, ValidationReplaySnapshot)
            and cached_cost_replay.settings_hash
            != session.request.settings_snapshot.rules_hash
            and cached_cost_replay.stock == session.request.stock
            and cached_cost_replay.timeframe_minutes
            == session.request.timeframe_minutes
            and cached_cost_replay.timeframe_key == session.request.timeframe_key
            and cached_cost_replay.evaluated_start_index == cached_start
            and cached_cost_replay.evaluated_end_index == cached_end
        ):
            if cancel_predicate is not None and cancel_predicate():
                raise ValueError("VALIDATION_REPLAY_CANCELLED")
            return ValidationReplayResult(
                ok=True,
                snapshot=self._rebind_trading_cost_replay_snapshot(
                    cached_cost_replay,
                    session.request.settings_snapshot.rules_hash,
                ),
            )

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
        native_options = ({"projected_candles_override": replay_candles}
                          if (active_pool.get("native_forming_included") is True
                              or (active_pool.get("timeframe_key") == "M1"
                                  and active_pool.get("calculation_source")
                                  == "SELECTED_DIRECT_AL_WITH_DIRECT_CURRENT"
                                  and active_pool.get("sor_m1_calculation_complete") is True))
                          else {})
        if callable(evaluate_with_context):
            if replay_start_index is None:
                replay_result = evaluate_with_context(
                    replay_historical,
                    context_provider=context_provider,
                    display_count=count,
                    **native_options,
                )
            else:
                replay_result = evaluate_with_context(
                    replay_historical,
                    context_provider=context_provider,
                    start_index=replay_start_index,
                    end_index=replay_end_index,
                    **native_options,
                )
        elif callable(evaluate):
            if replay_start_index is None:
                replay_result = evaluate(
                    replay_historical,
                    display_count=count,
                    **native_options,
                )
            else:
                replay_result = evaluate(
                    replay_historical,
                    start_index=replay_start_index,
                    end_index=replay_end_index,
                    **native_options,
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

    def _run_filter_valid_evidence(
        self,
        window: object,
        window_request: object,
    ) -> None:
        window_key = id(window)
        if not isinstance(window_request, dict):
            return
        if window_request.get("enabled") is not True:
            self._filter_valid_evidence_pending.pop(window_key, None)
            self._filter_valid_evidence_generation[window_key] = (
                self._filter_valid_evidence_generation.get(window_key, 0) + 1
            )
            return
        reject = getattr(window, "reject_filter_valid_evidence", None)
        if self._open_windows.get(window_key) is not window:
            if callable(reject):
                reject(window_request, "FILTER_VALID_WINDOW_UNAVAILABLE")
            return
        pool = self._historical_pools.get(window_key)
        session = self._validation_sessions.get(window_key)
        is_current = getattr(
            window,
            "_filter_valid_evidence_request_is_current",
            None,
        )
        if (
            not isinstance(pool, dict)
            or not isinstance(session, ValidationSession)
            or not callable(is_current)
            or not is_current(window_request)
        ):
            if callable(reject) and callable(is_current) and is_current(window_request):
                reject(window_request, "FILTER_VALID_SOURCE_UNAVAILABLE")
            return
        try:
            source_identity = self._pool_computation_source_identity(pool)
            dispatch_snapshot = self._pool_dispatch_snapshot(
                pool,
                source_identity=source_identity,
            )
        except Exception as exc:
            if callable(reject):
                reject(window_request, str(exc))
            return
        generation = self._filter_valid_evidence_generation.get(window_key, 0) + 1
        self._filter_valid_evidence_generation[window_key] = generation
        request = {
            "window": window,
            "window_key": window_key,
            "window_request": dict(window_request),
            "generation": generation,
            "request_generation": self._request_generation.get(window_key, 0),
            "pool": pool,
            "session": session,
            "source_identity": source_identity,
            "dispatch_snapshot": dispatch_snapshot,
            "history_target": self._history_targets.get(
                window_key,
                self._historical_count,
            ),
        }
        if window_key in self._filter_valid_evidence_active:
            self._filter_valid_evidence_pending[window_key] = request
            return
        self._start_filter_valid_evidence(request)

    def _filter_valid_evidence_request_is_current(
        self,
        request: object,
        *,
        verify_source: bool = False,
    ) -> bool:
        if not isinstance(request, dict):
            return False
        window_key = request.get("window_key")
        window = request.get("window")
        pool = request.get("pool")
        session = request.get("session")
        current = (
            isinstance(window_key, int)
            and self._open_windows.get(window_key) is window
            and self._filter_valid_evidence_generation.get(window_key)
            == request.get("generation")
            and self._request_generation.get(window_key, 0)
            == request.get("request_generation")
            and self._historical_pools.get(window_key) is pool
            and self._validation_sessions.get(window_key) is session
        )
        if not current:
            return False
        if not verify_source:
            return True
        window_current = getattr(
            window,
            "_filter_valid_evidence_request_is_current",
            None,
        )
        return bool(
            callable(window_current)
            and window_current(request.get("window_request"))
            and isinstance(pool, dict)
            and self._pool_computation_source_identity(pool)
            == request.get("source_identity")
        )

    def _start_filter_valid_evidence(self, request: dict[str, object]) -> None:
        if not self._filter_valid_evidence_request_is_current(request):
            return
        window_key = int(request["window_key"])
        token = object()
        self._filter_valid_evidence_active[window_key] = token

        def current() -> bool:
            return (
                self._filter_valid_evidence_active.get(window_key) is token
                and self._filter_valid_evidence_request_is_current(request)
                and window_key not in self._filter_valid_evidence_pending
            )

        @_perf_stage("worker.filter_valid_evidence", owner=request["window"])
        def prepare() -> None:
            payload = {
                "window_key": window_key,
                "generation": request["generation"],
                "request_generation": request["request_generation"],
                "token": token,
                "request": request,
                "window_request": request["window_request"],
                "identities_by_side": None,
                "signal_count": 0,
                "error": "",
                "cancelled": False,
            }
            try:
                if not current():
                    payload["cancelled"] = True
                    self._emit_filter_valid_evidence_completed(payload)
                    return
                pool = request["pool"]
                session = request["session"]
                worker_pool = dict(pool)
                worker_pool.pop("_dispatch_snapshot", None)
                worker_pool.update(deepcopy(request["dispatch_snapshot"]))
                result = self._prepare_replay_result_from_pool(
                    session,
                    worker_pool,
                    history_target=int(request["history_target"]),
                    evaluation_count=int(request["history_target"]),
                    cancel_predicate=lambda: not current(),
                    verified_source_identity=request["source_identity"],
                )
                if (
                    not isinstance(result, ValidationReplayResult)
                    or result.ok is not True
                    or result.snapshot is None
                ):
                    raise ValueError(self._failure_text("REPLAY", result))
                snapshot = result.snapshot
                if (
                    snapshot.stock != session.request.stock
                    or snapshot.settings_hash
                    != session.request.settings_snapshot.rules_hash
                    or snapshot.timeframe_key != session.request.timeframe_key
                    or snapshot.timeframe_minutes
                    != session.request.timeframe_minutes
                ):
                    raise ValueError("FILTER_VALID_EVIDENCE_IDENTITY_MISMATCH")
                rules = session.request.settings_snapshot.to_dict()
                descriptors = build_validation_filter_universe(rules)
                identities_by_side: dict[str, set[str]] = {
                    "BUY": set(),
                    "SELL": set(),
                }
                signal_count = 0
                for entry in snapshot.to_entries():
                    side = str(entry.evaluation_side or "").strip().upper()
                    if entry.signal != entry.evaluation_side or side not in identities_by_side:
                        continue
                    signal_count += 1
                    side_descriptors = tuple(
                        descriptor
                        for descriptor in descriptors
                        if side in {
                            str(value or "").strip().upper()
                            for value in descriptor.sides
                        }
                    )
                    identities_by_side[side].update(
                        active_filter_identities_for_entry(
                            entry,
                            rules,
                            side_descriptors,
                        )
                    )
                if not current():
                    payload["cancelled"] = True
                else:
                    payload["identities_by_side"] = {
                        side: tuple(sorted(values))
                        for side, values in identities_by_side.items()
                    }
                    payload["signal_count"] = signal_count
            except Exception as exc:
                if not current():
                    payload["cancelled"] = True
                else:
                    payload["error"] = str(exc)
            self._emit_filter_valid_evidence_completed(payload)

        try:
            Thread(
                target=prepare,
                name="indicator-follow-filter-valid-evidence",
                daemon=True,
            ).start()
        except Exception as exc:
            self._on_filter_valid_evidence_completed({
                "window_key": window_key,
                "generation": request["generation"],
                "request_generation": request["request_generation"],
                "token": token,
                "request": request,
                "window_request": request["window_request"],
                "identities_by_side": None,
                "signal_count": 0,
                "error": str(exc),
                "cancelled": False,
            })

    def _emit_filter_valid_evidence_completed(self, payload: object) -> bool:
        if sip.isdeleted(self):
            return False
        try:
            self.filter_valid_evidence_completed.emit(payload)
        except RuntimeError:
            if sip.isdeleted(self):
                return False
            raise
        return True

    def _on_filter_valid_evidence_completed(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        window_key = payload.get("window_key")
        token = payload.get("token")
        if (
            not isinstance(window_key, int)
            or self._filter_valid_evidence_active.get(window_key) is not token
        ):
            return
        self._filter_valid_evidence_active.pop(window_key, None)
        pending = self._filter_valid_evidence_pending.pop(window_key, None)
        request = payload.get("request")
        window = self._open_windows.get(window_key)
        if pending is not None:
            if self._filter_valid_evidence_request_is_current(pending):
                self._start_filter_valid_evidence(pending)
            return
        if (
            window is None
            or payload.get("cancelled") is True
            or not isinstance(request, dict)
        ):
            return
        if not self._filter_valid_evidence_request_is_current(
            request,
            verify_source=True,
        ):
            discard = getattr(window, "discard_filter_valid_evidence", None)
            if callable(discard):
                discard(payload.get("window_request"))
            return
        error = str(payload.get("error") or "").strip()
        if error:
            reject = getattr(window, "reject_filter_valid_evidence", None)
            if callable(reject):
                reject(payload.get("window_request"), error)
            return
        identities = payload.get("identities_by_side")
        apply_evidence = getattr(window, "apply_filter_valid_evidence", None)
        if callable(apply_evidence):
            apply_evidence(payload.get("window_request"), identities)

    def _range_replay_history_view(self, session, pool, history_target):
        if (pool.get("timeframe_key") != session.request.timeframe_key
                or pool.get("timeframe_minutes") != session.request.timeframe_minutes):
            # Raw M1 must never be relabelled as a derived M3 calculation pool.
            raise ValueError("RANGE_REPLAY_SOURCE_TIMEFRAME_MISMATCH")
        if not isinstance(pool.get("candles"), list) or not pool["candles"]:
            raise ValueError("HISTORICAL_POOL_EMPTY")
        # Match the normal chart's available prefix, including exhausted history.
        view, _ = self._normal_base_history_view(session, pool, history_target)
        return view

    def _normal_base_history_view(
        self, session, pool, history_target, display_view=None, *,
        sidecar_override=_SIDECAR_UNSET,
    ):
        self._require_selected_timeframe_pool(session, pool)
        warmup = required_validation_history_context_bars(session.request.settings_snapshot.to_dict())
        view = (self._validation_pool_for_session(
            session, pool, target_count=history_target + warmup,
            sidecar_override=sidecar_override)
            if display_view is None else (
                dict(display_view)
                if (display_view.get("validation_composite_calculation") is True
                    or pool.get("native_live_snapshot") is not None
                    or (display_view.get("calculation_source")
                        == "SELECTED_DIRECT_AL_WITH_DIRECT_CURRENT"
                        and display_view.get("sor_m1_calculation_complete") is True))
                else dict(pool,
                    candles=(display_view["candles"] if any(
                        row.get("display_only_special") for row in
                        display_view.get("display_candles", ())
                    ) else pool["candles"]),
                    display_candles=display_view.get("display_candles", display_view["candles"]),
                    display_only_integrated=display_view.get("display_only_integrated") is True)))
        # The selected view owns the calculation axis, including its special-bar exclusion.
        rows = view["candles"]
        display = view.get("display_candles", view["candles"])[-history_target:]
        first = next((i for i, row in enumerate(rows)
                      if display and row["time"] >= display[0]["time"]), len(rows))
        start = max(0, first - warmup)
        result = dict(view, candles=rows[start:], display_candles=display)
        result["requested_count"] = len(result["candles"])
        result["snapshot"] = ValidationHistoricalSnapshot(
            stock=session.request.stock,
            timeframe_minutes=session.request.timeframe_minutes,
            timeframe_key=session.request.timeframe_key,
            request_id=str(view["request_id"]),
            market_data_identity=str(view["market_data_identity"]),
            market_source=str(view["market_source"]),
            requested_count=max(1, result["requested_count"]),
            rows=self._historical_rows_from_candles(result["candles"]))
        return result, max(0, warmup - first)

    def _ensure_normal_base_history(self, window, session, pool, evaluation_count,
                                    *, display_view=None, on_ready=None):
        history_target = self._history_targets.get(id(window), self._historical_count)
        view, deficit = self._normal_base_history_view(session, pool, history_target, display_view)
        if not deficit:
            return False
        display = deepcopy(view["display_candles"])
        original_pool = pool
        source_identity = self._pool_computation_source_identity(pool)
        rows = deepcopy(pool["candles"])
        warmup = required_validation_history_context_bars(session.request.settings_snapshot.to_dict())

        def prepend(older):
            nonlocal rows, deficit
            # Retain current source values and exact display endpoints, including NXT.
            rows = merge_validation_candles(
                [c for c in older if c["time"] < rows[0]["time"]], rows,
                DEFAULT_CANDLES_MAX_COUNT)
            first = next((i for i, row in enumerate(rows)
                          if display and row["time"] >= display[0]["time"]), len(rows))
            deficit = max(0, warmup - first)

        # A covering cache's requested_count is its request envelope,
        # never the number of historical rows retained. Count only real
        # chronological growth and stop if a cache adds no older candles.
        count = len(rows)
        while deficit and count < DEFAULT_CANDLES_MAX_COUNT:
            entry = self._cached_history(session.request.stock, session.request.timeframe_minutes,
                                         session.request.timeframe_key, count + 1)
            if entry is None:
                break
            before = len(rows)
            prepend(entry.candles)
            count = len(rows)
            if count <= before:
                break

        # The source already answered a request sufficient to cover the
        # displayed span + hidden context. It returned fewer calculation bars,
        # or M5 closing rows were verified for display only.
        # Render the calculable indicators from those bars immediately;
        # do not issue the same redundant request and keep the chart blank.
        source_request_count = pool.get("requested_count")
        if (
            on_ready is None
            and display_view is None
            and isinstance(source_request_count, int)
            and not isinstance(source_request_count, bool)
            and source_request_count >= history_target + warmup
            and (len(rows) < source_request_count
                 or (len(rows) == source_request_count
                     and view.get("display_only_integrated") is True
                     and any(row.get("display_only_special") for row in display)))
        ):
            return False

        window_key = id(window)
        generation = self._request_generation.get(window_key, 0)
        request_key = (window_key, generation)

        def publish():
            if (self._open_windows.get(window_key) is not window
                    or self._request_generation.get(window_key, 0) != generation
                    or self._pool_computation_source_identity(original_pool) != source_identity):
                return
            enriched = self._pool_from_candles(
                session, rows, requested_count=max(len(rows), int(original_pool["requested_count"])),
                request_id=str(original_pool["request_id"]),
                market_data_identity=str(original_pool["market_data_identity"]),
                market_source=str(original_pool["market_source"]),
                source_verified_at=original_pool.get("source_verified_at"),
                direct_selected_query=original_pool.get("direct_selected_query"),
                selected_direct_forming_snapshot=original_pool.get("selected_direct_forming_snapshot"))
            enriched_view = dict(view, candles=(
                [*rows, view["candles"][-1]]
                if view.get("calculation_source") == "SELECTED_DIRECT_AL_WITH_DIRECT_CURRENT"
                and view["candles"][-1]["time"] > rows[-1]["time"] else rows),
                display_candles=display)
            if enriched_view.get("calculation_source") == "SELECTED_DIRECT_AL_WITH_DIRECT_CURRENT":
                enriched_view["snapshot"] = ValidationHistoricalSnapshot(
                    stock=session.request.stock,
                    timeframe_minutes=session.request.timeframe_minutes,
                    timeframe_key=session.request.timeframe_key,
                    requested_count=len(enriched_view["candles"]),
                    request_id=str(view["snapshot"].request_id),
                    rows=self._historical_rows_from_candles(enriched_view["candles"]),
                    market_data_identity=str(original_pool["market_data_identity"]),
                    market_source=str(original_pool["market_source"]),
                    request_as_of=original_pool["selected_direct_forming_snapshot"]["request_as_of"])
            if on_ready is not None:
                on_ready(enriched, enriched_view, deficit)
                return
            self._use_pool_for_window(window, session, enriched,
                                      evaluation_count=evaluation_count,
                                      _normal_view=enriched_view)

        if not deficit:
            publish()
            return True
        try:
            requester = _V5SelectedSourceRequester(
                self._broker, session.request.stock, session.request.timeframe_key,
                self._validation_source_by_code[session.request.stock.code],
                on_verified_period_response=self._capture_sor_period_prior_close,
                now_factory=self._now_factory,
                on_verified_native_response=self._remember_native_receipt,
            )
            provider = self._historical_provider_factory(
                session, requester,
                **self._broker_al_m1_provider_options(session),
            )
            request_latest = provider.request_latest
        except Exception:
            publish()
            return True

        def request_more(request_count):
            if (self._open_windows.get(window_key) is not window
                    or self._request_generation.get(window_key, 0) != generation):
                return
            self._active_providers[request_key] = provider

            def completed(result):
                if self._active_providers.get(request_key) is not provider:
                    return
                self._active_providers.pop(request_key, None)
                if self._request_generation.get(window_key, 0) != generation:
                    return
                self._retain_broker_al_m1_result(window_key, result)
                before = len(rows)
                if self._historical_result_matches_validation_source(result, session.request.stock):
                    try:
                        incoming, _, _ = self._direct_selected_history_parts(result.snapshot)
                        prepend(incoming)
                    except Exception:
                        incoming = []
                    if deficit and len(rows) > before and len(incoming) >= request_count:
                        next_count = min(DEFAULT_CANDLES_MAX_COUNT, request_count + deficit)
                        if next_count > request_count:
                            QTimer.singleShot(0, lambda: request_more(next_count))
                            return
                publish()

            try:
                request_latest(request_count, completed)
            except Exception:
                self._active_providers.pop(request_key, None)
                publish()

        request_more(min(DEFAULT_CANDLES_MAX_COUNT, max(len(rows), count) + deficit))
        return True

    def _use_pool_for_window(
        self,
        window: object,
        session: ValidationSession,
        pool: dict[str, object],
        *,
        evaluation_count: int,
        source_identity: tuple[object, ...] | None = None,
        _normal_view: dict[str, object] | None = None,
        live_m1_snapshot_key: tuple[object, ...] | None = None,
    ) -> None:
        if (_normal_view is None and getattr(window, "_normal_base_requested", False)
                and self._ensure_normal_base_history(
                    window, session, pool, evaluation_count)):
            return
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
            "normal_base_view": _normal_view,
            "live_m1_snapshot_key": live_m1_snapshot_key,
            "live_native_request_id": (
                pool["native_live_snapshot"]["request_id"]
                if isinstance(pool.get("native_live_snapshot"), dict) else None),
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

    def _start_normal_base_prepare(self, request: dict[str, object], history_target: int) -> None:
        """Use the canonical history source to compute only display indicator series."""
        window = request["window"]
        session = request["session"]
        window_key = id(window)
        token = request["token"]
        observation_job = f"{os.getpid()}:{window_key}:{request['generation']}:{id(token)}"
        settings = session.request.settings_snapshot
        rules = settings.to_dict()
        descriptors = window._filter_display_descriptors(rules)
        detached_pool = dict(request["pool"])
        detached_pool.pop("_dispatch_snapshot", None)
        inflight = self._history_extension_inflight.get(window_key)
        cache_lease = (
            inflight.get("cache_lease", _CACHE_LEASE_UNSET)
            if isinstance(inflight, dict) else _CACHE_LEASE_UNSET
        )
        self._prepare_active[window_key] = token

        def current() -> bool:
            return (self._prepare_active.get(window_key) is token
                    and self._prepare_request_is_current(request)
                    and window_key not in self._prepare_pending
                    and self._history_targets.get(window_key, self._historical_count) == history_target)

        @_perf_stage("worker.normal_base", owner=window)
        def prepare() -> None:
            observation_started_at = datetime.now(SEOUL_TIMEZONE).isoformat()
            payload = dict(window_key=window_key, generation=request["generation"],
                           prepare_token=token, session=session, pool=request["pool"],
                           evaluation_count=request["evaluation_count"], history_target=history_target,
                           normal_base=True,
                           live_m1_snapshot_key=request.get("live_m1_snapshot_key"),
                           live_native_request_id=request.get("live_native_request_id"))
            try:
                detached_pool.update(deepcopy(request["dispatch_snapshot"]))
                view, _ = self._normal_base_history_view(
                    session, detached_pool, history_target, request.get("normal_base_view"))
                marking_source_view = view
                live_key = request.get("live_m1_snapshot_key")
                if live_key is not None:
                    try:
                        frozen = request.get("sor_m1_calculation_snapshot")
                        proof = frozen[2] if isinstance(frozen, tuple) and len(frozen) == 3 else None
                        live_calendar = (
                            session.request.timeframe_key in {"D1", "W1", "MO1", "Y1"}
                            and isinstance(live_key, tuple) and len(live_key) == 4
                            and isinstance(proof, dict)
                            and proof.get("forming_snapshot_source") in _V5_SOR_FORMING_SOURCES
                            and proof.get("forming_snapshot_received_at") == live_key[3]
                        )
                        view = self._sor_m1_calculation_view(
                            session, detached_pool, view, frozen,
                            live_calendar=live_calendar,
                            normal_base_only=request.get("normal_base_only") is True,
                        )
                    except (KeyError, TypeError, ValueError):
                        payload["live_sor_signal"] = {"unavailable": True}
                else:
                    view = self._sor_m1_calculation_view(
                        session, detached_pool, view,
                        request.get("sor_m1_calculation_snapshot"),
                        normal_base_only=request.get("normal_base_only") is True,
                    )
                if self._verified_sor_tail_provider is not None:
                    now = self._now_factory()
                    now = (now.replace(tzinfo=SEOUL_TIMEZONE) if now.tzinfo is None
                           else now.astimezone(SEOUL_TIMEZONE))
                    view = self._verified_sor_view_for_session(
                        session, detached_pool, view, now,
                        request.get("dispatch_source_identity"),
                    )
                calculation = view["candles"]
                display = view.get("display_candles", calculation)[-history_target:]
                indexes = (_calculation_indexes_for_mixed_display(calculation, display)
                           if view.get("display_only_integrated") is True
                           else _calculation_indexes_for_display(calculation, display))
                cache, _active = _build_validation_projection_data(
                    calculation, [], [], indexes, rules, descriptors,
                    cancel_predicate=lambda: not current())
                payload["prepared_view_pool"] = view
                payload["normal_base_presentation"] = (descriptors, cache)
                if (session.request.timeframe_key == "M1" and live_key is not None
                        and view.get("sor_m1_calculation_complete") is True):
                    payload["m1_marking_basis"] = (
                        marking_source_view, request.get("sor_m1_calculation_snapshot"))
                if request.get("live_native_request_id") is not None:
                    candles = view.get("candles")
                    native_signal = {"unavailable": True}
                    if (current() and isinstance(candles, list) and candles
                            and view.get("native_live_snapshot", {}).get("request_id")
                            == request["live_native_request_id"]):
                        try:
                            from routines.지표추종매매.routine import evaluate_current_signal_selection
                            selected = evaluate_current_signal_selection(
                                candles, rules,
                                {"candles": candles,
                                 "_indicator_follow_validation_current_price": candles[-1]["close"]},
                            )
                            signal = str(selected.get("signal") or "").strip().upper()
                            native_signal = {
                                "signal": signal if signal in {"BUY", "SELL"} else None,
                                "conflict": bool(selected.get("signal_conflict_evidence")),
                            }
                        except Exception:
                            pass
                    native_signal.update(stock_code=session.request.stock.code,
                                         timeframe_key=session.request.timeframe_key)
                    payload["live_native_signal"] = native_signal
                if live_key is not None and "live_sor_signal" not in payload:
                    projected = request.get("sor_m1_calculation_snapshot")
                    proof = projected[2] if isinstance(projected, tuple) and len(projected) == 3 else None
                    candles = view.get("candles")
                    try:
                        source_minute = datetime.fromisoformat(live_key[2])
                        target_start = datetime.strptime(
                            candles[-1]["time"], "%Y%m%d%H%M%S",
                        ).replace(tzinfo=SEOUL_TIMEZONE)
                    except (KeyError, TypeError, ValueError, IndexError):
                        source_minute = target_start = None
                    calendar_match = False
                    if (source_minute is not None and target_start is not None
                            and target_start.date() <= source_minute.date()):
                        timeframe = session.request.timeframe_key
                        calendar_match = (
                            (timeframe == "D1" and target_start.date() == source_minute.date())
                            or (timeframe == "W1" and target_start.isocalendar()[:2]
                                == source_minute.isocalendar()[:2])
                            or (timeframe == "MO1" and (target_start.year, target_start.month)
                                == (source_minute.year, source_minute.month))
                            or (timeframe == "Y1" and target_start.year == source_minute.year)
                        )
                    if (not isinstance(live_key, tuple) or len(live_key) != 4
                             or not isinstance(proof, dict)
                             or proof.get("forming_snapshot_source") not in _V5_SOR_FORMING_SOURCES
                             or proof.get("forming_snapshot_received_at") != live_key[3]
                             or view.get("sor_m1_calculation_complete") is not True
                             or view.get("market_source") != "INTEGRATED"
                             or view.get("market_data_identity") != f"{session.request.stock.code}_AL"
                             or not current()
                             or not isinstance(candles, list) or not candles
                             or (session.request.timeframe_key != "M1" and
                                 (not display or display[-1] != candles[-1]))
                             or source_minute is None or target_start is None
                             or source_minute.utcoffset() != timedelta(hours=9)
                             or not (calendar_match or target_start <= source_minute < (
                                 target_start + timedelta(minutes=session.request.timeframe_minutes)
                             ))
                             or candles[-1].get("close") is None):
                        payload["live_sor_signal"] = {"unavailable": True}
                    else:
                        try:
                            from routines.지표추종매매.routine import evaluate_current_signal_selection
                            selected = evaluate_current_signal_selection(
                                candles, rules,
                                {"candles": candles,
                                 "_indicator_follow_validation_current_price": candles[-1]["close"]},
                            )
                            signal = str(selected.get("signal") or "").strip().upper()
                            payload["live_sor_signal"] = {
                                "signal": signal if signal in {"BUY", "SELL"} else None,
                                "conflict": bool(selected.get("signal_conflict_evidence")),
                            }
                        except Exception:
                            payload["live_sor_signal"] = {"unavailable": True}
                if live_key is not None:
                    payload["live_sor_signal"]["stock_code"] = session.request.stock.code
                    payload["live_sor_signal"]["timeframe_key"] = session.request.timeframe_key
                if isinstance(inflight, dict) and current():
                    self._persist_history(
                        session.request.stock,
                        int(detached_pool["timeframe_minutes"]),
                        str(detached_pool["timeframe_key"]),
                        int(detached_pool["requested_count"]),
                        detached_pool["candles"],
                        market_data_identity=str(detached_pool.get("market_data_identity") or ""),
                        market_source=str(detached_pool.get("market_source") or ""),
                        cache_lease=cache_lease,
                        native_source_receipt=detached_pool.get("native_minute_receipt"),
                        direct_selected_query=detached_pool.get("direct_selected_query"),
                    )
            except Exception as exc:
                payload["error"] = str(exc)
            if not current():
                payload["cancelled"] = True
            try:
                if _m3_observation.ENABLED and session.request.stock.code == "005930" \
                        and session.request.timeframe_key == "M3":
                    observed_view = locals().get("view")
                    observed_view = observed_view if isinstance(observed_view, dict) else {}
                    proof = observed_view.get("native_live_snapshot") or detached_pool.get("native_live_snapshot")
                    proof = proof if isinstance(proof, dict) else {}
                    direct = detached_pool.get("selected_direct_forming_snapshot")
                    direct = direct if isinstance(direct, dict) else {}
                    source_rows = observed_view.get("candles")
                    display_rows = observed_view.get("display_candles", source_rows)
                    forming = proof.get("forming_candle")
                    selected = _m3_observation.record(
                        "prepare", stock_code="005930", timeframe="M3",
                        rate_key=("prepare",), job=observation_job,
                        window_key=window_key, generation=request["generation"],
                        started_at=observation_started_at,
                        finished_at=datetime.now(SEOUL_TIMEZONE).isoformat(),
                        outcome=("error" if payload.get("error") else
                                 "cancelled" if payload.get("cancelled") else "finished"),
                        error=payload.get("error"), request_id=(
                            proof.get("request_id") or direct.get("request_id") or
                            request.get("live_native_request_id")),
                        tick_revision=proof.get("tick_revision"),
                        source_time=(forming.get("tick_received_at") or forming.get("snapshot_received_at")
                                     if isinstance(forming, dict) else direct.get("request_as_of")),
                        source=observed_view.get("market_source"),
                        identity=observed_view.get("market_data_identity"),
                        current_snapshot_source=(forming.get("snapshot_source")
                                                 if isinstance(forming, dict) else None),
                        native_forming_included=observed_view.get("native_forming_included"),
                        forming_exclusion_reason=("NATIVE_FORMING_NOT_FRESH_AT_VIEW"
                                                  if proof and observed_view.get("native_forming_included") is False
                                                  else None),
                        forming_status=observed_view.get("forming_bar_status"),
                        calculation_source=observed_view.get("calculation_source"),
                        rules_hash=settings.rules_hash,
                        historical_pool_count=(len(detached_pool.get("candles"))
                                               if isinstance(detached_pool.get("candles"), list) else None),
                        historical_pool_tail=_m3_observation.tail(detached_pool.get("candles")),
                        native_completed_count=(len(proof.get("completed_candles"))
                                                if isinstance(proof.get("completed_candles"), list) else None),
                        native_forming_tail=(_m3_observation.tail((forming,))
                                             if isinstance(forming, dict) else None),
                        direct_forming_tail=(_m3_observation.tail((direct["candle"],))
                                             if isinstance(direct.get("candle"), dict) else None),
                        calculation_count=len(source_rows) if isinstance(source_rows, list) else None,
                        calculation_tail=_m3_observation.tail(source_rows),
                        display_count=len(display_rows) if isinstance(display_rows, list) else None,
                        display_tail=_m3_observation.tail(display_rows),
                        signal=payload.get("live_native_signal") or payload.get("live_sor_signal"))
                    if selected:
                        payload["m3_observation_job"] = observation_job
                        payload["m3_observation_selected"] = True
            except Exception:
                pass  # Optional observation cannot suppress completion.
            self._emit_signal_scan_completed(payload)

        try:
            Thread(target=prepare, name="indicator-follow-normal-base", daemon=True).start()
        except Exception:
            self._prepare_active.pop(window_key, None)
            raise

    def _verified_sor_view_for_session(
        self, session, source_pool, view, as_of, dispatch_source_identity,
    ):
        """Bind a detached V5 calculation view to the dispatched source and tail."""
        if isinstance(source_pool, dict) and source_pool.get("market_source") in {"NXT", "KRX"}:
            self._require_selected_timeframe_pool(session, source_pool)
            self._require_selected_timeframe_pool(session, view)
            return view
        if isinstance(view, dict) and view.get("validation_composite_calculation") is True:
            unmixed = view.get("_validation_sor_view")
            if not isinstance(unmixed, dict):
                raise ValueError("SOR_VERIFIED_BLOCKED:COMPOSITE_SOURCE_UNAVAILABLE")
            verified = self._verified_sor_view_for_session(
                session, source_pool, unmixed, as_of, dispatch_source_identity,
            )
            sidecar = self._integrated_display_sidecars.get(
                self._integrated_display_sidecar_key(session)
            )
            return self._compose_verified_nxt_pre(
                session, verified,
                self._verified_validation_premarket_candles(session, sidecar),
                sidecar,
                source_pool.get("candles"),
            )
        if self._verified_sor_tail_provider is None:
            return view
        if (not isinstance(dispatch_source_identity, tuple)
                or self._pool_computation_source_identity(source_pool)
                != dispatch_source_identity):
            raise ValueError("SOR_VERIFIED_BLOCKED:SOURCE_CHANGED_AFTER_DISPATCH")
        history_sha256 = (
            dispatch_source_identity[-1]
            if len(dispatch_source_identity) >= 9 else ""
        )
        try:
            batch = self._verified_sor_tail_provider(
                session, history_sha256, as_of,
            )
        except Exception as exc:
            raise ValueError(
                f"SOR_VERIFIED_BLOCKED:PROVIDER_{type(exc).__name__}"
            ) from exc
        view = apply_verified_sor_tail(
            view, stock_code=session.request.stock.code,
            timeframe_key=session.request.timeframe_key,
            timeframe_minutes=session.request.timeframe_minutes,
            as_of=as_of, history_sha256=history_sha256, batch=batch,
        )
        view["snapshot"] = ValidationHistoricalSnapshot(
            stock=session.request.stock,
            timeframe_minutes=session.request.timeframe_minutes,
            timeframe_key=session.request.timeframe_key,
            requested_count=len(view["candles"]),
            request_id=f"{view.get('request_id') or 'POOL'}:SOR_TAIL",
            rows=self._historical_rows_from_candles(view["candles"]),
            market_data_identity=str(view["market_data_identity"]),
            market_source=str(view["market_source"]),
            request_as_of=as_of.isoformat(),
        )
        return view

    def _capture_sor_period_prior_close(
        self, code, timeframe_key, response, started,
        request_started_at, received_at, request_session=None, callback_session=None,
    ) -> None:
        """Retain a broker-observed post-session cumulative row for later guarded use."""
        trcodes = {"W1": "opt10082", "MO1": "opt10083", "Y1": "opt10094"}
        root = getattr(self._historical_cache, "root", None)
        if (not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code)
                or timeframe_key not in trcodes or root is None
                or not isinstance(started, dict) or started.get("ok") is not True
                or any(started.get(key, expected) != expected for key, expected in (
                    ("code", code), ("market_data_identity", f"{code}_AL"),
                    ("market_source", "INTEGRATED"),
                    ("timeframe_key", timeframe_key),
                ))
                or not isinstance(response, dict)
                or response.get("code") != code
                or response.get("market_data_identity") != f"{code}_AL"
                or response.get("market_source") != "INTEGRATED"
                or response.get("timeframe_key") != timeframe_key
                or response.get("trcode") != trcodes[timeframe_key]
                or response.get("has_more") is not False
                or response.get("warning") not in (None, "")
                or not response.get("request_id")
                or response.get("request_id") != response.get("rqname")):
            return
        try:
            start = request_started_at.astimezone(SEOUL_TIMEZONE)
            received = received_at.astimezone(SEOUL_TIMEZONE)
        except (AttributeError, TypeError, ValueError):
            return
        if (request_started_at.utcoffset() is None or received_at.utcoffset() is None
                or start.date() != received.date() or start > received
                or start.hour < 20 or received.hour < 20 or received.weekday() > 4):
            return
        rows = response.get("rows")
        if (not isinstance(rows, list) or not rows
                or response.get("rows_count") != len(rows)):
            return
        times = [row.get("\uccb4\uacb0\uc2dc\uac04") if isinstance(row, dict) else None
                 for row in rows]
        if any(not isinstance(stamp, str) or len(stamp) != 14 or not stamp.isdigit()
               for stamp in times) or len(times) != len(set(times)):
            return
        period_start = max(times)
        try:
            period_date = datetime.strptime(period_start, "%Y%m%d%H%M%S").date()
        except ValueError:
            return
        if (period_start[8:] != "000000" or period_date > received.date()
                or (timeframe_key == "W1" and
                    period_date.isocalendar()[:2] != received.date().isocalendar()[:2])
                or (timeframe_key == "MO1" and
                    (period_date.year, period_date.month) !=
                    (received.year, received.month))
                or (timeframe_key == "Y1" and period_date.year != received.year)):
            return
        row = rows[times.index(period_start)]
        fields = {
            "open": "\uc2dc\uac00", "high": "\uace0\uac00", "low": "\uc800\uac00",
            "close": "\ud604\uc7ac\uac00", "volume": "\uac70\ub798\ub7c9",
        }
        try:
            values = {name: abs(float(str(row[field]).strip()))
                      for name, field in fields.items()}
        except (KeyError, TypeError, ValueError):
            return
        if (not all(math.isfinite(value) for value in values.values())
                or values["low"] <= 0
                or not values["low"] <= values["open"] <= values["high"]
                or not values["low"] <= values["close"] <= values["high"]):
            return
        payload = {
            "schema_version": 1, "stock_code": code,
            "market_data_identity": f"{code}_AL", "market_source": "INTEGRATED",
            "timeframe_key": timeframe_key, "trcode": trcodes[timeframe_key],
            "request_id": response["request_id"],
            "broker_request_status": started.get("status", "UNREPORTED"),
            "request_started_at": start.isoformat(),
            "broker_callback_received_at": received.isoformat(),
            "capture_date": received.date().isoformat(),
            "period_start": period_start,
            "cumulative_ohlcv": values,
            "provenance": "MATCHED_BROKER_CALLBACK_NO_SESSION_EPOCH_OR_FINALITY_ATTESTATION",
        }
        epoch = getattr(request_session, "connection_epoch", None)
        login = getattr(request_session, "login_session_id", None)
        if (getattr(request_session, "connected", None) is True
                and getattr(callback_session, "connected", None) is True
                and type(epoch) is int and epoch > 0
                and isinstance(login, str) and bool(login)
                and getattr(callback_session, "connection_epoch", None) == epoch
                and getattr(callback_session, "login_session_id", None) == login):
            payload["broker_session"] = {
                "request_connected": True, "callback_connected": True,
                "request_connection_epoch": epoch,
                "request_login_session_id": login,
                "callback_connection_epoch": getattr(callback_session, "connection_epoch"),
                "callback_login_session_id": getattr(callback_session, "login_session_id"),
            }
            payload["provenance"] = "SESSION_BOUND_BROKER_AL_POST_CLOSE_OBSERVATION"
        path = Path(root) / f"{code}_AL_{timeframe_key}_prior_close.json"
        if path.is_symlink():
            return
        if path.exists():
            try:
                previous = json.loads(path.read_text(encoding="utf-8"))
                previous_at = datetime.fromisoformat(previous["broker_callback_received_at"])
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                return
            if (previous.get("stock_code") != code
                    or previous.get("timeframe_key") != timeframe_key
                    or previous.get("market_data_identity") != f"{code}_AL"
                    or previous_at.tzinfo is None or previous_at >= received):
                return
        temp_path = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temp_name = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent),
            )
            temp_path = Path(temp_name)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, allow_nan=False,
                          sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, path)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()

    def _sor_period_prior_close(self, code, timeframe_key, period_start, as_of):
        """Admit only a session-bound prior trading day's post-close observation."""
        root = getattr(self._historical_cache, "root", None)
        if root is None:
            raise ValueError("SOR_M1_BLOCKED:PERIOD_CUMULATIVE_SOURCE_UNAVAILABLE")
        path = Path(root) / f"{code}_AL_{timeframe_key}_prior_close.json"
        try:
            if path.is_symlink():
                raise ValueError("untrusted path")
            record = json.loads(path.read_text(encoding="utf-8"))
            captured = datetime.fromisoformat(record["broker_callback_received_at"])
            started = datetime.fromisoformat(record["request_started_at"])
            day = datetime.fromisoformat(record["capture_date"]).date()
            period_day = datetime.strptime(period_start, "%Y%m%d%H%M%S").date()
            values = record["cumulative_ohlcv"]
            broker_session = record["broker_session"]
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise ValueError("SOR_M1_BLOCKED:PERIOD_CUMULATIVE_SOURCE_UNAVAILABLE") from None
        today = as_of.date()
        gap = (today - day).days
        expected_gap = 3 if today.weekday() == 0 and day.weekday() == 4 else 1
        trcode = {"W1": "opt10082", "MO1": "opt10083", "Y1": "opt10094"}[timeframe_key]
        if (record.get("schema_version") != 1
                or record.get("stock_code") != code
                or record.get("market_data_identity") != f"{code}_AL"
                or record.get("market_source") != "INTEGRATED"
                or record.get("timeframe_key") != timeframe_key
                or record.get("trcode") != trcode
                or not isinstance(record.get("request_id"), str)
                or not record["request_id"]
                or record.get("provenance") != "SESSION_BOUND_BROKER_AL_POST_CLOSE_OBSERVATION"
                or not isinstance(broker_session, dict)
                or broker_session.get("request_connected") is not True
                or broker_session.get("callback_connected") is not True
                or type(broker_session.get("request_connection_epoch")) is not int
                or broker_session["request_connection_epoch"] <= 0
                or not isinstance(broker_session.get("request_login_session_id"), str)
                or not broker_session["request_login_session_id"]
                or broker_session.get("callback_connection_epoch")
                   != broker_session["request_connection_epoch"]
                or broker_session.get("callback_login_session_id")
                   != broker_session["request_login_session_id"]
                or record.get("period_start") != period_start
                or period_start[8:] != "000000" or period_day > day
                or (timeframe_key == "W1" and
                    period_day.isocalendar()[:2] != day.isocalendar()[:2])
                or (timeframe_key == "MO1" and
                    (period_day.year, period_day.month) != (day.year, day.month))
                or (timeframe_key == "Y1" and period_day.year != day.year)
                or gap != expected_gap or day.weekday() > 4 or today.weekday() > 4
                or started.utcoffset() != timedelta(hours=9)
                or captured.utcoffset() != timedelta(hours=9)
                or started.date() != day or captured.date() != day
                or started.hour < 20 or captured.hour < 20
                or not started <= captured
                or not isinstance(values, dict)
                or set(values) != {"open", "high", "low", "close", "volume"}
                or any(type(v) not in (int, float) or not math.isfinite(v)
                       for v in values.values())
                or values["low"] <= 0 or values["volume"] < 0
                or not values["low"] <= values["open"] <= values["high"]
                or not values["low"] <= values["close"] <= values["high"]):
            raise ValueError("SOR_M1_BLOCKED:PERIOD_CUMULATIVE_SOURCE_UNAVAILABLE")
        return values

    @staticmethod
    def _sor_m3_after_anchor_verified(pool, as_of):
        """Require the selected direct _AL M3 source to prove the broker's AFTER clock."""
        if (pool.get("timeframe_key") != "M3" or pool.get("timeframe_minutes") != 3
                or pool.get("market_source") != "INTEGRATED"
                or pool.get("market_data_identity")
                != f"{getattr(pool.get('stock'), 'code', '')}_AL"):
            return False
        direct = pool.get("candles")
        if not isinstance(direct, list) or not direct or any(
            not isinstance(row, dict) for row in direct
        ):
            return False
        stamps = [str(row.get("time") or "") for row in direct]
        today = as_of.strftime("%Y%m%d")
        prior = [stamp for stamp in stamps if stamp[:8] < today]
        if (any(len(stamp) != 14 or not stamp.isdigit() for stamp in stamps)
                or stamps != sorted(set(stamps)) or not prior):
            return False
        newest_day = prior[-1][:8]
        after = [stamp for stamp in prior if stamp[8:12] >= "1530"]
        return (f"{newest_day}153900" in after and all(
            "1530" <= stamp[8:12] < "2000"
            and (int(stamp[8:10]) * 60 + int(stamp[10:12])
                 - (930 if stamp[8:12] < "1539" else 939)) % 3 == 0
            and stamp[12:] == "00" and stamp < as_of.strftime("%Y%m%d%H%M%S")
            for stamp in after
        ))

    @staticmethod
    def _sor_m15_after_anchor_verified(pool, as_of):
        """Use a partial 15:30 bucket only when selected direct _AL M15 proves it."""
        if (pool.get("timeframe_key") != "M15" or pool.get("timeframe_minutes") != 15
                or pool.get("market_source") != "INTEGRATED"
                or pool.get("market_data_identity")
                != f"{getattr(pool.get('stock'), 'code', '')}_AL"):
            return False
        direct = pool.get("candles")
        if not isinstance(direct, list) or not direct or any(
            not isinstance(row, dict) for row in direct
        ):
            return False
        stamps = [str(row.get("time") or "") for row in direct]
        cutoff = as_of.strftime("%Y%m%d%H%M%S")
        if (any(len(stamp) != 14 or not stamp.isdigit() or stamp[12:] != "00"
                for stamp in stamps)
                or stamps != sorted(set(stamps)) or stamps[-1] >= cutoff):
            return False
        try:
            for stamp in stamps:
                datetime.strptime(stamp, "%Y%m%d%H%M%S")
        except ValueError:
            return False
        prior = [stamp for stamp in stamps if stamp[:8] < cutoff[:8]]
        if not prior:
            return False
        # The latest prior source day is the trading-day witness; no holiday guess.
        newest_day = prior[-1][:8]
        newest_after = {stamp for stamp in prior
                        if stamp.startswith(newest_day) and stamp[8:12] >= "1530"}
        if not {f"{newest_day}153000", f"{newest_day}154500"}.issubset(newest_after):
            return False
        return all(
            "1530" <= stamp[8:12] < "2000"
            and (int(stamp[8:10]) * 60 + int(stamp[10:12]) - 930) % 15 == 0
            for stamp in prior if stamp[8:12] >= "1530"
        )

    def _remember_published_m1_marking_basis(self, window, session, pool, view,
                                             generation, basis):
        window._v5_m1_marking_basis = None
        if (session.request.stock.code != "005930"
                or session.request.timeframe_key != "M1"
                or self._verified_sor_tail_provider is not None
                or pool.get("native_live_snapshot") is not None
                or not isinstance(basis, tuple) or len(basis) != 2):
            return
        source_view, frozen = basis
        publication = pool.get("_dispatch_snapshot")
        calculation = getattr(window, "_calculation_candles", None)
        display = getattr(window, "_candles", None)
        snapshot = view.get("snapshot")
        if (self._open_windows.get(id(window)) is not window
                or self._historical_pools.get(id(window)) is not pool
                or self._validation_sessions.get(id(window)) is not session
                or self._request_generation.get(id(window), 0) != generation
                or not isinstance(publication, tuple) or len(publication) != 4
                or publication[3] != self._pool_computation_source_identity(pool)
                or not isinstance(source_view, dict)
                or source_view.get("timeframe_key") != "M1"
                or source_view.get("market_data_identity") != "005930_AL"
                or source_view.get("market_source") != "INTEGRATED"
                or not isinstance(frozen, tuple) or len(frozen) != 3
                or frozen[0] != publication[3]
                or not isinstance(frozen[1], datetime)
                or not isinstance(frozen[2], dict)
                or frozen[2].get("projection") != "FORMING_BASE_BAR"
                or frozen[2].get("available") is not True
                or frozen[2].get("forming_snapshot_source") not in _V5_SOR_FORMING_SOURCES
                or view.get("calculation_source")
                   != "SELECTED_HISTORY_WITH_BROKER_AL_CONFIRMED_M1_TODAY"
                or view.get("sor_m1_calculation_complete") is not True
                or not isinstance(calculation, list) or not calculation
                or not isinstance(display, list) or not display
                or calculation != view.get("candles")
                or display != view.get("display_candles", [])[-len(display):]
                or not isinstance(snapshot, ValidationHistoricalSnapshot)
                or snapshot.to_rows() != self._historical_rows_from_candles(calculation)):
            return
        packed = pickle.dumps((source_view, frozen, calculation, display), protocol=4)
        window._v5_m1_marking_basis = (
            pool, session, generation, publication, packed)

    def _published_m1_marking_basis(self, window, session, pool, generation,
                                    request):
        basis = getattr(window, "_v5_m1_marking_basis", None)
        if basis is None:
            return None
        if not isinstance(basis, tuple) or len(basis) != 5:
            raise ValueError("SOR_VERIFIED_BLOCKED:MARKING_SOURCE_CHANGED")
        bound_pool, bound_session, bound_generation, publication, packed = basis
        if (bound_pool is not pool or bound_session is not session
                or bound_generation != generation
                or self._historical_pools.get(id(window)) is not pool
                or self._validation_sessions.get(id(window)) is not session
                or self._request_generation.get(id(window), 0) != generation
                or pool.get("_dispatch_snapshot") is not publication
                or not isinstance(publication, tuple) or len(publication) != 4
                or not isinstance(publication[2], dict)
                or publication[3] != self._pool_computation_source_identity(pool)
                or publication[2].get("direct_selected_query")
                   != pool.get("direct_selected_query")
                or publication[2].get("selected_direct_forming_snapshot")
                   != pool.get("selected_direct_forming_snapshot")
                or not isinstance(packed, bytes)):
            raise ValueError("SOR_VERIFIED_BLOCKED:MARKING_SOURCE_CHANGED")
        try:
            source_view, frozen, calculation, display = pickle.loads(packed)
        except (pickle.UnpicklingError, EOFError, TypeError, ValueError) as exc:
            raise ValueError("SOR_VERIFIED_BLOCKED:MARKING_SOURCE_CHANGED") from exc
        if (not isinstance(source_view, dict)
                or not isinstance(frozen, tuple) or len(frozen) != 3
                or frozen[0] != publication[3]
                or not isinstance(calculation, list)
                or not isinstance(display, list)
                or calculation != getattr(window, "_calculation_candles", None)
                or display != getattr(window, "_candles", None)
                or calculation != request.get("calculation")
                or display != request.get("display")):
            raise ValueError("SOR_VERIFIED_BLOCKED:MARKING_SOURCE_CHANGED")
        return source_view, frozen, basis

    def _remember_published_direct_current(self, window, session, pool, view,
                                           generation):
        """Bind the installed M3 calculation to its detached direct response."""
        if session.request.stock.code != "005930" or session.request.timeframe_key != "M3":
            return
        window._v5_direct_current_replay_basis = None
        window_key = id(window)
        if (self._open_windows.get(window_key) is not window
                or self._historical_pools.get(window_key) is not pool
                or self._validation_sessions.get(window_key) is not session
                or self._request_generation.get(window_key, 0) != generation):
            return
        publication = pool.get("_dispatch_snapshot")
        snapshot = view.get("snapshot")
        frozen = view.get("_direct_current_calculation_snapshot")
        if (view.get("calculation_source") != "SELECTED_DIRECT_AL_WITH_DIRECT_CURRENT"
                or pool.get("native_live_snapshot") is not None
                or not isinstance(snapshot, ValidationHistoricalSnapshot)
                or not isinstance(publication, tuple) or len(publication) != 4
                or not isinstance(publication[2], dict)
                or not isinstance(frozen, tuple) or len(frozen) != 3
                or frozen[0] != publication[3]
                or not isinstance(frozen[1], datetime)
                or frozen[2] != {"projection": "DIRECT_COMPLETED_TIMEFRAME"}):
            return
        window._v5_direct_current_replay_basis = (
            pool, session, generation,
            publication, snapshot, frozen,
        )

    def _published_direct_current_snapshot(self, window, session, pool, generation):
        basis = getattr(window, "_v5_direct_current_replay_basis", None)
        if not isinstance(basis, tuple) or len(basis) != 6:
            return None
        bound_pool, bound_session, bound_generation, publication, snapshot, frozen = basis
        calculation = getattr(window, "_calculation_candles", None)
        if (bound_pool is not pool or bound_session is not session
                or bound_generation != generation
                or pool.get("_dispatch_snapshot") is not publication
                or pool.get("native_live_snapshot") is not None
                or self._validation_source_by_code.get("005930") is not True
                or not isinstance(calculation, list) or not calculation
                or not isinstance(snapshot, ValidationHistoricalSnapshot)
                or snapshot.to_rows() != self._historical_rows_from_candles(calculation)
                or publication[3] != self._pool_computation_source_identity(pool)
                or not isinstance(frozen, tuple) or len(frozen) != 3
                or frozen[0] != publication[3]
                or not isinstance(frozen[1], datetime)
                or frozen[2] != {"projection": "DIRECT_COMPLETED_TIMEFRAME"}
                or publication[2].get("direct_selected_query") != pool.get("direct_selected_query")
                or publication[2].get("selected_direct_forming_snapshot")
                   != pool.get("selected_direct_forming_snapshot")):
            return None
        receipt = publication[2]["selected_direct_forming_snapshot"]
        query = publication[2]["direct_selected_query"]
        if (not isinstance(receipt, dict) or not isinstance(query, dict)
                or receipt.get("request_as_of") != query.get("request_as_of")):
            return None
        received = self._source_snapshot_datetime(receipt["request_as_of"])
        candle = receipt.get("candle")
        end = (self._direct_selected_completion_time(candle, 3)
               if isinstance(candle, dict) else None)
        if (received is None or end is None or frozen[1].tzinfo is None
                or not received <= frozen[1] < end):
            return None
        return frozen

    def _sor_m1_calculation_snapshot(self, session, pool, *, live_forming=False):
        """Freeze M1 only for the current forming bar; completed minute bars stay direct."""
        code = session.request.stock.code
        if (self._verified_sor_tail_provider is not None
                or self._validation_source_by_code.get(code) is not True
                or pool.get("market_source") != "INTEGRATED"
                or pool.get("market_data_identity") != f"{code}_AL"):
            return None
        as_of = self._now_factory()
        as_of = (as_of.replace(tzinfo=SEOUL_TIMEZONE) if as_of.tzinfo is None
                 else as_of.astimezone(SEOUL_TIMEZONE))
        if (session.request.timeframe_key in _LIVE_MINUTE_TIMEFRAMES - {"M1"}
                and not live_forming):
            return (self._pool_computation_source_identity(pool), as_of,
                    {"projection": "DIRECT_COMPLETED_TIMEFRAME"})
        owner = self.parent()
        getter = getattr(owner, "existing_market_data_host", None)
        market = getter() if callable(getter) else None
        required = getattr(market, "_sor_m1_required_by_stock", None)
        if not isinstance(required, dict) or code not in required:
            return None
        try:
            if not live_forming:
                from gui_market_data_host import MarketDataHost

                live_session = (
                    as_of.weekday() < 5
                    and MarketDataHost._display_session_for_time(as_of)
                    in {"NXT_PRE", "KRX", "NXT_AFTER"}
                )
                if not live_session:
                    verified_m1 = MarketDataHost._current_display_candles(
                        market, code, 1,
                    )
                    newest = verified_m1[-1] if verified_m1 else None
                    latest = candle_market_datetime(newest) if newest else None
                    if (isinstance(newest, dict)
                            and newest.get("market_data_identity") == f"{code}_AL"
                            and newest.get("market_source") == "INTEGRATED"
                            and newest.get("display_only") is True
                            and newest.get("is_complete") is True
                            and latest is not None
                            and latest.utcoffset() == timedelta(hours=9)
                            and latest.date() < as_of.date()):
                        # Replay only the independently verified last trading day.
                        # This does not change any live trading-time projection.
                        as_of = latest.replace(second=0, microsecond=0) + timedelta(minutes=1)
            period = session.request.timeframe_key in {"W1", "MO1", "Y1"}
            higher_live = (live_forming and session.request.timeframe_key in
                           _LIVE_MINUTE_TIMEFRAMES - {"M1"})
            rules = ({"bar": {"bar_minutes": 1}} if period or session.request.timeframe_key == "D1"
                     else session.request.settings_snapshot.to_dict())
            session_windows = None
            if higher_live:
                from gui_market_data_host import MarketDataHost, _DISPLAY_INTEGRATED_SESSIONS
                m3_after_anchor = (
                    session.request.timeframe_key == "M3"
                    and 15 * 60 + 40 <= as_of.hour * 60 + as_of.minute < 20 * 60
                    and self._sor_m3_after_anchor_verified(pool, as_of)
                )
                m15_after_anchor = (
                    session.request.timeframe_key == "M15"
                    and 15 * 60 + 40 <= as_of.hour * 60 + as_of.minute < 20 * 60
                    and self._sor_m15_after_anchor_verified(pool, as_of)
                )
                session_windows = tuple({
                    "name": name, "start_time": f"{start // 60:02d}:{start % 60:02d}",
                    "end_time": f"{end // 60:02d}:{end % 60:02d}",
                    **({"bucket_anchor_time": "15:39" if m3_after_anchor else "15:30"}
                       if (m3_after_anchor or m15_after_anchor) and name == "NXT_AFTER"
                       else {}),
                } for name, start, end in _DISPLAY_INTEGRATED_SESSIONS)
            projected = market.project_broker_sor_m1_calculation(
                stock_code=code, rules=rules,
                projection_request={"projection": (
                    "FORMING_BASE_BAR" if session.request.timeframe_key == "D1" or period or live_forming
                    else "COMPLETED_TIMEFRAME"
                )},
                as_of=as_of,
                **({"session_windows": session_windows} if higher_live else {}),
            )
            if higher_live and projected.get("available") is True:
                cutoff = as_of.replace(second=0, microsecond=0)
                candidates = projected.get("candles")
                target = candle_market_datetime(candidates[-1]) if candidates else None
                source = MarketDataHost._current_display_candles(market, code, 1)
                covered = {candle_market_datetime(row) for row in source}
                expected_start = (max(target, cutoff.replace(hour=15, minute=40))
                                  if (m3_after_anchor or m15_after_anchor)
                                  and target is not None
                                  and target.hour * 60 + target.minute >= 930 else target)
                expected = (set(expected_start + timedelta(minutes=minute) for minute in
                                range(int((cutoff - expected_start).total_seconds() // 60)))
                             if target is not None and target <= cutoff else set())
                projected["forming_target_source_covered"] = (
                    target is not None and target.date() == cutoff.date()
                    and target <= cutoff and expected.issubset(covered)
                )
        except Exception as exc:
            projected = {"available": False,
                         "availability_state": f"PROJECTION_{type(exc).__name__}"}
        return (self._pool_computation_source_identity(pool), as_of,
                deepcopy(projected))

    def _fresh_selected_d1_view(self, session, source_pool, view, as_of,
                                expected_calculation):
        if (session.request.timeframe_key != "D1" or not isinstance(source_pool, dict)
                or not isinstance(view, dict) or not isinstance(as_of, datetime)):
            return False
        code = session.request.stock.code
        query = source_pool.get("direct_selected_query")
        rows = source_pool.get("candles")
        historical = view.get("candles")
        display = view.get("display_candles", historical)
        selected_at = (self._source_snapshot_datetime(query.get("request_as_of"))
                       if isinstance(query, dict) else None)
        observed_at = self._source_snapshot_datetime(self._now_factory())
        if (not isinstance(query, dict) or selected_at is None or observed_at is None
                or selected_at.utcoffset() != timedelta(hours=9)
                or selected_at.date() != as_of.date()
                or not timedelta(0) <= as_of - selected_at < timedelta(minutes=1)
                or not timedelta(0) <= observed_at - selected_at < timedelta(minutes=1)
                or query.get("stock_code") != code
                or query.get("timeframe_key") != "D1"
                or query.get("timeframe_minutes") != session.request.timeframe_minutes
                or query.get("market_source") != "INTEGRATED"
                or query.get("market_data_identity") != f"{code}_AL"
                or source_pool.get("request_id") not in (
                    query.get("request_id"), f"{query.get('request_id')}:CACHE_MERGE")
                or not isinstance(rows, list) or not rows
                or not isinstance(historical, list) or not historical
                or not isinstance(display, list) or not display
                or source_pool.get("timeframe_key") != view.get("timeframe_key")
                or view.get("timeframe_key") != "D1"
                or source_pool.get("timeframe_minutes") != view.get("timeframe_minutes")
                or source_pool.get("market_source") != view.get("market_source")
                or view.get("market_source") != "INTEGRATED"
                or source_pool.get("market_data_identity") != view.get("market_data_identity")
                or view.get("market_data_identity") != f"{code}_AL"
                or view.get("source_coverage_sufficient") is False
                or view.get("display_only_integrated") is True
                or historical != rows[-len(historical):]
                or display != historical[-len(display):]):
            return False
        stamps = [str(row.get("time") or "") for row in rows if isinstance(row, dict)]
        last = rows[-1]
        values = tuple(last.get(field) for field in
                       ("open", "high", "low", "close", "volume"))
        return (len(stamps) == len(rows) and stamps == sorted(set(stamps))
                and all(len(stamp) == 14 and stamp.isdigit() for stamp in stamps)
                and stamps[-1] == as_of.strftime("%Y%m%d") + "000000"
                and all(row.get("is_complete") is not False for row in rows[:-1])
                and all(stamp[:8] < as_of.strftime("%Y%m%d") for stamp in stamps[:-1])
                and len(values) == 5
                and all(type(value) in (int, float) and math.isfinite(value)
                        for value in values)
                and values[2] > 0 and values[4] > 0
                and values[2] <= values[0] <= values[1]
                and values[2] <= values[3] <= values[1]
                and query.get("selected_row_sha256") == hashlib.sha256(json.dumps(
                    last, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"), allow_nan=False,
                ).encode("utf-8")).hexdigest()
                and (expected_calculation is None or
                     (isinstance(expected_calculation, list) and expected_calculation
                      and expected_calculation == historical[-len(expected_calculation):])))

    def _fresh_selected_m1_view(self, session, source_pool, view, as_of,
                                expected_calculation):
        from gui_market_data_host import MarketDataHost

        code = session.request.stock.code
        query = source_pool.get("direct_selected_query")
        receipt = source_pool.get("selected_direct_forming_snapshot")
        rows = source_pool.get("candles")
        historical = view.get("candles") if isinstance(view, dict) else None
        display = (view.get("display_candles") or historical
                   if isinstance(view, dict) else None)
        selected_at = (self._source_snapshot_datetime(query.get("request_as_of"))
                       if isinstance(query, dict) else None)
        observed_at = self._source_snapshot_datetime(self._now_factory())
        candle = receipt.get("candle") if isinstance(receipt, dict) else None
        if (isinstance(view, dict) and isinstance(candle, dict)
                and view.get("calculation_source") == "SELECTED_DIRECT_AL_WITH_DIRECT_CURRENT"
                and view.get("sor_m1_calculation_complete") is True
                and isinstance(historical, list) and historical
                and isinstance(display, list) and display
                and historical[-1] == candle and display[-1] == candle):
            historical = historical[:-1]
            display = display[:-1]
        start = candle_market_datetime(candle) if isinstance(candle, dict) else None
        end = (self._direct_selected_completion_time(candle, 1)
               if isinstance(candle, dict) else None)
        latest = MarketDataHost._expected_latest_completed_display_start(as_of, 1)
        if (code != "005930" or not isinstance(query, dict)
                or not isinstance(receipt, dict) or not isinstance(candle, dict)
                or not isinstance(rows, list) or not rows
                or not isinstance(historical, list) or not historical
                or not isinstance(display, list) or not display
                or selected_at is None or observed_at is None
                or start is None or end is None or latest is None
                or selected_at.utcoffset() != timedelta(hours=9)
                or not start <= selected_at <= as_of <= observed_at < end
                or MarketDataHost._display_session_for_time(as_of)
                    not in {"NXT_PRE", "KRX", "NXT_AFTER"}
                or MarketDataHost._display_session_for_time(start)
                    != MarketDataHost._display_session_for_time(as_of)
                or source_pool.get("request_id") != query.get("request_id")
                or any(receipt.get(field) != query.get(field) for field in
                       ("stock_code", "timeframe_key", "timeframe_minutes",
                        "market_data_identity", "market_source", "request_id",
                        "request_as_of"))
                or not self._direct_selected_query_valid(
                    query, session.request.stock, "M1", 1, f"{code}_AL", rows)
                or query.get("source_content_sha256") != hashlib.sha256(json.dumps(
                    rows, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"), allow_nan=False,
                ).encode("utf-8")).hexdigest()
                or query.get("selected_forming_sha256") != hashlib.sha256(json.dumps(
                    candle, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"), allow_nan=False,
                ).encode("utf-8")).hexdigest()
                or source_pool.get("market_source") != view.get("market_source")
                or view.get("market_source") != "INTEGRATED"
                or source_pool.get("market_data_identity") != view.get("market_data_identity")
                or view.get("market_data_identity") != f"{code}_AL"
                or source_pool.get("timeframe_key") != view.get("timeframe_key")
                or view.get("timeframe_key") != "M1"
                or source_pool.get("timeframe_minutes") != view.get("timeframe_minutes")
                or view.get("timeframe_minutes") != 1
                or view.get("source_coverage_sufficient") is False
                or view.get("display_only_integrated") is True
                or historical != rows[-len(historical):]
                or display != historical[-len(display):]
                or rows[-1]["time"] != latest.strftime("%Y%m%d%H%M%S")
                or candle.get("is_complete") is True
                or candle.get("market_data_identity", f"{code}_AL") != f"{code}_AL"
                or candle.get("market_source", "INTEGRATED") != "INTEGRATED"
                or candle.get("timeframe_minutes", 1) != 1
                or start.second != 0 or candle.get("time") <= rows[-1]["time"]):
            return None
        stamps = [str(row.get("time") or "") for row in rows if isinstance(row, dict)]
        if (len(stamps) != len(rows) or stamps != sorted(set(stamps))
                or any(len(stamp) != 14 or not stamp.isdigit() for stamp in stamps)):
            return None
        for row in [*rows, candle]:
            values = tuple(row.get(field) for field in
                           ("open", "high", "low", "close", "volume"))
            if (row is not candle and row.get("is_complete") is False
                    or row.get("market_data_identity", f"{code}_AL") != f"{code}_AL"
                    or row.get("market_source", "INTEGRATED") != "INTEGRATED"
                    or row.get("timeframe_minutes", 1) != 1
                    or any(type(value) not in (int, float) or not math.isfinite(value)
                           for value in values)
                    or values[2] <= 0 or values[4] < 0
                    or not values[2] <= values[0] <= values[1]
                    or not values[2] <= values[3] <= values[1]):
                return None
        calculation = [*historical, candle]
        if (expected_calculation is not None
                and expected_calculation != calculation[-len(expected_calculation):]):
            raise ValueError("SOR_M1_BLOCKED:DISPLAYED_CALCULATION_CHANGED")
        return dict(view, candles=calculation, display_candles=calculation[-5000:],
                    calculation_source="SELECTED_DIRECT_AL_WITH_DIRECT_CURRENT",
                    sor_m1_calculation_complete=True,
                    production_calculation_cache_identity=(
                        "SOR_DIRECT_CURRENT", receipt["request_id"],
                        receipt["request_as_of"], query["selected_forming_sha256"]),
                    snapshot=ValidationHistoricalSnapshot(
                        stock=session.request.stock, timeframe_minutes=1,
                        timeframe_key="M1", requested_count=len(calculation),
                        request_id=f"{view.get('request_id') or 'POOL'}:DIRECT_CURRENT",
                        rows=self._historical_rows_from_candles(calculation),
                        market_data_identity=f"{code}_AL", market_source="INTEGRATED",
                        request_as_of=receipt["request_as_of"]))

    def _sor_m1_calculation_view(
        self, session, source_pool, view, frozen, expected_calculation=None,
        *, live_calendar=False, normal_base_only=False,
    ):
        if isinstance(source_pool, dict) and source_pool.get("native_live_snapshot") is not None:
            self._require_selected_timeframe_pool(session, source_pool)
            selected = (view.get("_validation_sor_view")
                        if isinstance(view, dict)
                        and view.get("validation_composite_calculation") is True else view)
            self._require_selected_timeframe_pool(session, selected)
            return view
        if isinstance(source_pool, dict) and source_pool.get("market_source") in {"NXT", "KRX"}:
            self._require_selected_timeframe_pool(session, source_pool)
            self._require_selected_timeframe_pool(session, view)
            return view
        if (frozen is not None and isinstance(view, dict)
                and view.get("validation_composite_calculation") is True):
            unmixed = view.get("_validation_sor_view")
            if not isinstance(unmixed, dict):
                raise ValueError("SOR_M1_BLOCKED:COMPOSITE_SOURCE_UNAVAILABLE")
            projected = self._sor_m1_calculation_view(
                session, source_pool, unmixed, frozen, expected_calculation,
                live_calendar=live_calendar, normal_base_only=normal_base_only,
            )
            sidecar = self._integrated_display_sidecars.get(
                self._integrated_display_sidecar_key(session)
            )
            return self._compose_verified_nxt_pre(
                session, projected,
                self._verified_validation_premarket_candles(session, sidecar),
                sidecar,
                source_pool.get("candles"),
            )
        if frozen is None:
            code = session.request.stock.code
            if (self._verified_sor_tail_provider is None
                    and self._validation_source_by_code.get(code) is True):
                now = self._now_factory()
                now = (now.replace(tzinfo=SEOUL_TIMEZONE) if now.tzinfo is None
                       else now.astimezone(SEOUL_TIMEZONE))
                today = now.strftime("%Y%m%d")
                candles = view.get("candles") if isinstance(view, dict) else None
                if not isinstance(candles, list) or not candles or any(
                    not isinstance(row, dict)
                    or len(str(row.get("time") or "")) != 14
                    or (str(row.get("time") or "")[:8] >= today
                        and not self._fresh_selected_d1_view(
                            session, source_pool, view, now, expected_calculation))
                    for row in candles
                ):
                    raise ValueError("SOR_M1_BLOCKED:SOURCE_UNAVAILABLE")
            return view
        timeframe_key = session.request.timeframe_key
        period = timeframe_key in {"W1", "MO1", "Y1"}
        d1 = timeframe_key == "D1"
        source_identity, as_of, projected = frozen
        live_minute_forming = (timeframe_key in _LIVE_MINUTE_TIMEFRAMES
                               and isinstance(projected, dict)
                               and projected.get("projection") == "FORMING_BASE_BAR")
        live_higher_forming = live_minute_forming and timeframe_key != "M1"
        forming = d1 or period or live_minute_forming
        if self._pool_computation_source_identity(source_pool) != source_identity:
            raise ValueError("SOR_M1_BLOCKED:SOURCE_CHANGED_AFTER_SNAPSHOT")
        if timeframe_key in _LIVE_MINUTE_TIMEFRAMES - {"M1"}:
            m3_after_anchor_verified = (timeframe_key == "M3"
                                        and self._sor_m3_after_anchor_verified(source_pool, as_of))
            m15_after_anchor_verified = (timeframe_key == "M15"
                                         and self._sor_m15_after_anchor_verified(source_pool, as_of))
            if (live_higher_forming and timeframe_key == "M3"
                    and 15 * 60 + 40 <= as_of.hour * 60 + as_of.minute < 20 * 60
                    and not m3_after_anchor_verified):
                raise ValueError("SOR_M1_BLOCKED:BROKER_AFTER_ANCHOR_UNVERIFIED")
            if (live_higher_forming and timeframe_key == "M15"
                    and 15 * 60 + 40 <= as_of.hour * 60 + as_of.minute < 20 * 60
                    and not m15_after_anchor_verified):
                raise ValueError("SOR_M1_BLOCKED:BROKER_AFTER_ANCHOR_UNVERIFIED")
            code = session.request.stock.code
            historical = view.get("candles") if isinstance(view, dict) else None
            display = view.get("display_candles", historical) if isinstance(view, dict) else None
            direct_m5_special = (timeframe_key == "M5" and isinstance(display, list)
                                 and any(isinstance(row, dict) and row.get("display_only_special")
                                         == NATIVE_CLOSING_PRICE_SPECIAL for row in display))
            if (not isinstance(view, dict)
                    or source_pool.get("market_source") != "INTEGRATED"
                    or source_pool.get("market_data_identity") != f"{code}_AL"
                    or source_pool.get("timeframe_key") != timeframe_key
                    or source_pool.get("timeframe_minutes") != session.request.timeframe_minutes
                    or view.get("market_source") != "INTEGRATED"
                    or view.get("market_data_identity") != f"{code}_AL"
                    or view.get("timeframe_key") != timeframe_key
                    or view.get("timeframe_minutes") != session.request.timeframe_minutes
                    or not isinstance(historical, list) or not historical
                    or not isinstance(display, list) or not display
                    or view.get("source_coverage_sufficient") is False
                    or (view.get("display_only_integrated") is True and not direct_m5_special)):
                raise ValueError("SOR_M1_BLOCKED:SOR_DIRECT_HISTORY_UNAVAILABLE")
            stamps = [str(row.get("time") or "") for row in historical
                      if isinstance(row, dict)]
            settings = getattr(session.request, "settings_snapshot", None)
            rules = settings.to_dict() if settings is not None else {}
            scope = rules.get("validation_market_scope") if isinstance(rules, dict) else None
            regular_only = (isinstance(scope, dict)
                            and scope.get("regular_market_only") is True)
            expected_display = (_krx_regular_session_candles(historical)
                                if regular_only else historical[-len(display):])
            if (len(stamps) != len(historical)
                    or any(len(stamp) != 14 or not stamp.isdigit() for stamp in stamps)
                    or stamps != sorted(set(stamps))
                    or any(row.get("is_complete") is False for row in historical)
                    or (display != expected_display and not direct_m5_special)):
                raise ValueError("SOR_M1_BLOCKED:SOR_DIRECT_HISTORY_PROVENANCE_INVALID")
            if direct_m5_special:
                source_rows = source_pool.get("candles")
                if not isinstance(source_rows, list) or not source_rows:
                    raise ValueError("SOR_M1_BLOCKED:SOR_DIRECT_HISTORY_PROVENANCE_INVALID")
                special_rows = [row for row in source_rows if isinstance(row, dict)
                                and str(row.get("time") or "").endswith("153500")]
                expected_display = [dict(row, display_only_special=NATIVE_CLOSING_PRICE_SPECIAL)
                                    if row in special_rows else dict(row) for row in source_rows
                                    if isinstance(row, dict)]
                expected_direct_history = [row for row in source_rows if row not in special_rows]
                if (len(expected_display) != len(source_rows)
                        or display != expected_display[-len(display):]
                        or historical != expected_direct_history[-len(historical):]
                        or len({row["time"][:8] for row in special_rows}) != len(special_rows)):
                    raise ValueError("SOR_M1_BLOCKED:SOR_DIRECT_HISTORY_PROVENANCE_INVALID")
            broker_timed_history = timeframe_key in {
                "M5", "M10", "M30", "M60", "M120", "M240"
            }
            native_after_history = (timeframe_key in {"M3", "M15"}
                and self._direct_selected_query_valid(
                    source_pool.get("direct_selected_query"), session.request.stock,
                    timeframe_key, session.request.timeframe_minutes,
                    f"{code}_AL", source_pool.get("candles")))
            if broker_timed_history and not self._direct_selected_query_valid(
                    source_pool.get("direct_selected_query"), session.request.stock,
                    timeframe_key, session.request.timeframe_minutes,
                    f"{code}_AL", source_pool.get("candles")):
                raise ValueError("SOR_M1_BLOCKED:SOR_DIRECT_HISTORY_PROVENANCE_INVALID")
            from gui_market_data_host import MarketDataHost, _DISPLAY_INTEGRATED_SESSIONS
            for row, stamp in zip(historical, stamps):
                try:
                    bar_start = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(
                        tzinfo=SEOUL_TIMEZONE)
                except ValueError:
                    raise ValueError("SOR_M1_BLOCKED:SOR_DIRECT_HISTORY_PROVENANCE_INVALID") from None
                session_name = MarketDataHost._display_session_for_time(bar_start)
                session_span = next(((start, end) for name, start, end in
                                     _DISPLAY_INTEGRATED_SESSIONS if name == session_name), None)
                session_end = session_span[1] if session_span else None
                anchor = session_span[0] if session_span else 15 * 60 + 30
                if (session_end is None and (timeframe_key in {"M3", "M15"}
                                            or broker_timed_history)
                        and 15 * 60 + 30 <= bar_start.hour * 60 + bar_start.minute
                        < 15 * 60 + 40):
                    session_end = 15 * 60 + 40
                if session_name == "NXT_AFTER":
                    if timeframe_key == "M3" and (m3_after_anchor_verified or native_after_history):
                        anchor = 15 * 60 + 39
                    elif timeframe_key == "M15" and (m15_after_anchor_verified or native_after_history):
                        anchor = 15 * 60 + 30
                if (session_end is None or bar_start.second != 0
                        or (not broker_timed_history
                            and session_name != "KRX_CLOSING_AUCTION"
                            and (bar_start.hour * 60 + bar_start.minute - anchor)
                            % session.request.timeframe_minutes != 0)
                        or row.get("market_data_identity", f"{code}_AL") != f"{code}_AL"
                        or row.get("market_source", "INTEGRATED") != "INTEGRATED"
                        or row.get("timeframe_minutes", session.request.timeframe_minutes)
                        != session.request.timeframe_minutes
                        or min(bar_start + timedelta(minutes=session.request.timeframe_minutes),
                               bar_start.replace(hour=session_end // 60,
                                                 minute=session_end % 60)) > as_of):
                    raise ValueError("SOR_M1_BLOCKED:SOR_DIRECT_HISTORY_PROVENANCE_INVALID")
            direct_current = None
            receipt = source_pool.get("selected_direct_forming_snapshot")
            if receipt is not None and not live_higher_forming:
                query = source_pool.get("direct_selected_query")
                candle = receipt.get("candle") if isinstance(receipt, dict) else None
                received = self._source_snapshot_datetime(
                    receipt.get("request_as_of")) if isinstance(receipt, dict) else None
                start = candle_market_datetime(candle) if isinstance(candle, dict) else None
                end = (self._direct_selected_completion_time(candle, session.request.timeframe_minutes)
                       if isinstance(candle, dict) else None)
                current_session = (MarketDataHost._display_session_for_time(start)
                                   if start is not None else None)
                current_span = next(((first, last) for name, first, last in
                                     _DISPLAY_INTEGRATED_SESSIONS if name == current_session), None)
                current_anchor = current_span[0] if current_span else 15 * 60 + 30
                if current_session == "NXT_AFTER":
                    if timeframe_key == "M3" and (m3_after_anchor_verified or native_after_history):
                        current_anchor = 15 * 60 + 39
                    elif timeframe_key == "M15" and (m15_after_anchor_verified or native_after_history):
                        current_anchor = 15 * 60 + 30
                completed_end = self._direct_selected_completion_time(
                    historical[-1], session.request.timeframe_minutes)
                values = (tuple(candle.get(field) for field in
                                ("open", "high", "low", "close", "volume"))
                          if isinstance(candle, dict) else ())
                if (not self._direct_selected_query_valid(
                        query, session.request.stock, timeframe_key,
                        session.request.timeframe_minutes, f"{code}_AL",
                        source_pool.get("candles"))
                        or not isinstance(receipt, dict)
                        or any(receipt.get(field) != query.get(field) for field in
                               ("stock_code", "timeframe_key", "timeframe_minutes",
                                "market_data_identity", "market_source", "request_id",
                                "request_as_of"))
                        or not isinstance(candle, dict)
                        or candle.get("is_complete") is True
                        or candle.get("market_data_identity", f"{code}_AL") != f"{code}_AL"
                        or candle.get("market_source", "INTEGRATED") != "INTEGRATED"
                        or candle.get("timeframe_minutes", session.request.timeframe_minutes)
                           != session.request.timeframe_minutes
                        or start is None or end is None or received is None
                        or completed_end is None or completed_end > start
                        or (current_span is None and not (
                            current_session == "AL_TRANSITION"
                            and (timeframe_key in {"M3", "M15"} or broker_timed_history)
                            and 15 * 60 + 30 <= start.hour * 60 + start.minute < 15 * 60 + 40))
                        or start.second != 0
                        or (not broker_timed_history
                            and current_session != "KRX_CLOSING_AUCTION"
                            and (start.hour * 60 + start.minute - current_anchor)
                            % session.request.timeframe_minutes != 0)
                        or received.utcoffset() != timedelta(hours=9)
                        or not start <= received < end
                        or candle.get("time") <= stamps[-1]
                        or len(values) != 5
                        or any(type(value) not in (int, float) or not math.isfinite(value)
                               for value in values)
                        or values[2] <= 0 or values[4] < 0
                        or not values[2] <= values[0] <= values[1]
                        or not values[2] <= values[3] <= values[1]):
                    raise ValueError("SOR_M1_BLOCKED:SOR_DIRECT_CURRENT_PROVENANCE_INVALID")
                if received <= as_of < end:
                    direct_current = candle
            if not live_higher_forming:
                if (as_of.weekday() < 5
                        and MarketDataHost._display_session_for_time(as_of)
                        in {"NXT_PRE", "KRX", "NXT_AFTER"}):
                    latest_direct = MarketDataHost._expected_latest_completed_display_start(
                        as_of, session.request.timeframe_minutes,
                    )
                    if (direct_current is not None and latest_direct is not None
                            and (m3_after_anchor_verified or m15_after_anchor_verified)):
                        latest_direct = min(
                            latest_direct,
                            candle_market_datetime(direct_current)
                            - timedelta(minutes=session.request.timeframe_minutes))
                    if (latest_direct is not None and
                            stamps[-1] < latest_direct.strftime("%Y%m%d%H%M%S")):
                        raise ValueError("SOR_M1_BLOCKED:SOR_DIRECT_HISTORY_UNAVAILABLE")
                if direct_current is not None:
                    calculation = [*historical, direct_current]
                    if (expected_calculation is not None
                            and expected_calculation != calculation[-len(expected_calculation):]):
                        raise ValueError("SOR_M1_BLOCKED:DISPLAYED_CALCULATION_CHANGED")
                    content = hashlib.sha256(json.dumps(
                        direct_current, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
                    return dict(view, candles=calculation,
                                display_candles=calculation[-5000:],
                                calculation_source="SELECTED_DIRECT_AL_WITH_DIRECT_CURRENT",
                                _direct_current_calculation_snapshot=frozen,
                                sor_m1_calculation_complete=True,
                                production_calculation_cache_identity=(
                                    "SOR_DIRECT_CURRENT", receipt["request_id"],
                                    receipt["request_as_of"], content),
                                snapshot=ValidationHistoricalSnapshot(
                                    stock=session.request.stock,
                                    timeframe_minutes=session.request.timeframe_minutes,
                                    timeframe_key=timeframe_key,
                                    requested_count=len(calculation),
                                    request_id=f"{view.get('request_id') or 'POOL'}:DIRECT_CURRENT",
                                    rows=self._historical_rows_from_candles(calculation),
                                    market_data_identity=f"{code}_AL",
                                    market_source="INTEGRATED",
                                    request_as_of=receipt["request_as_of"]))
                if (expected_calculation is not None
                        and expected_calculation != historical[-len(expected_calculation):]):
                    raise ValueError("SOR_M1_BLOCKED:DISPLAYED_CALCULATION_CHANGED")
                return view
            candidates = projected.get("candles") if isinstance(projected, dict) else None
            if (not isinstance(projected, dict)
                    or projected.get("available") is not True
                    or projected.get("forming_included") is not True
                    or projected.get("forming_target_source_covered") is not True
                    or not isinstance(candidates, list) or not candidates
                    or candidates[-1].get("is_complete") is not False
                    or projected.get("forming_snapshot_source")
                    not in _V5_SOR_FORMING_SOURCES):
                raise ValueError("SOR_M1_BLOCKED:SOR_FORMING_SOURCE_UNAVAILABLE")
            forming_stamp = candle_market_datetime(candidates[-1])
            if (forming_stamp is None or forming_stamp.date() != as_of.date()
                    or forming_stamp > as_of
                    or forming_stamp.strftime("%Y%m%d%H%M%S") <= stamps[-1]):
                raise ValueError("SOR_M1_BLOCKED:SOR_FORMING_SOURCE_UNAVAILABLE")
            from gui_market_data_host import MarketDataHost
            latest_direct = MarketDataHost._expected_latest_completed_display_start(
                as_of, session.request.timeframe_minutes,
            )
            if (latest_direct is not None and
                    (m3_after_anchor_verified or m15_after_anchor_verified)):
                latest_direct = min(
                    latest_direct,
                    forming_stamp - timedelta(minutes=session.request.timeframe_minutes),
                )
            if (latest_direct is not None
                    and latest_direct < forming_stamp
                    and stamps[-1] < latest_direct.strftime("%Y%m%d%H%M%S")):
                raise ValueError("SOR_M1_BLOCKED:SOR_DIRECT_HISTORY_UNAVAILABLE")
            forming_row = {"time": forming_stamp.strftime("%Y%m%d%H%M%S"),
                           **{field: candidates[-1][field] for field in
                              ("open", "high", "low", "close", "volume")}}
            calculation = [*historical, forming_row]
            if (expected_calculation is not None
                    and expected_calculation != calculation[-len(expected_calculation):]):
                raise ValueError("SOR_M1_BLOCKED:DISPLAYED_CALCULATION_CHANGED")
            result = dict(view, candles=calculation,
                          display_candles=([*display, forming_row])[-5000:],
                          calculation_source="SELECTED_DIRECT_AL_WITH_LIVE_SOR_FORMING",
                          sor_m1_calculation_complete=True,
                          production_calculation_cache_identity=(
                              "SOR_DIRECT_FORMING", projected.get("source_identity"),
                              as_of.isoformat()),
                          snapshot=ValidationHistoricalSnapshot(
                              stock=session.request.stock,
                              timeframe_minutes=session.request.timeframe_minutes,
                              timeframe_key=timeframe_key,
                              requested_count=len(calculation),
                              request_id=f"{view.get('request_id') or 'POOL'}:SOR_FORMING",
                              rows=self._historical_rows_from_candles(calculation),
                              market_data_identity=f"{code}_AL",
                              market_source="INTEGRATED",
                              request_as_of=projected["forming_snapshot_received_at"],
                          ))
            if isinstance(projected.get("sor_source_receipt"), dict):
                result["sor_source_receipt"] = deepcopy(projected["sor_source_receipt"])
            return result
        if (normal_base_only and timeframe_key in _LIVE_MINUTE_TIMEFRAMES - {"M1"}
                and isinstance(projected, dict)
                and projected.get("availability_state") == "SOR_M1_SOURCE_UNAVAILABLE"
                and isinstance(view, dict)
                and source_pool.get("market_source") == "INTEGRATED"
                and source_pool.get("market_data_identity") ==
                    f"{session.request.stock.code}_AL"
                and view.get("market_source") == "INTEGRATED"
                and view.get("market_data_identity") ==
                    source_pool.get("market_data_identity")
                and source_pool.get("timeframe_key") == timeframe_key
                and view.get("timeframe_key") == timeframe_key
                and source_pool.get("timeframe_minutes") == session.request.timeframe_minutes
                and view.get("timeframe_minutes") == session.request.timeframe_minutes
                and view.get("source_coverage_sufficient") is not False
                and view.get("display_only_integrated") is not True):
            from gui_market_data_host import MarketDataHost

            now = self._now_factory()
            now = (now.replace(tzinfo=SEOUL_TIMEZONE) if now.tzinfo is None
                   else now.astimezone(SEOUL_TIMEZONE))
            historical = view.get("candles")
            display = view.get("display_candles", historical)
            if (now.weekday() >= 5 or MarketDataHost._display_session_for_time(now)
                    not in {"NXT_PRE", "KRX", "NXT_AFTER"}):
                if (isinstance(historical, list) and historical
                        and isinstance(display, list) and display
                        and all(isinstance(row, dict) and row.get("is_complete") is not False
                                for row in historical)
                        and all(isinstance(row, dict) and row.get("is_complete") is not False
                                for row in display)):
                    stamps = [str(row.get("time") or "") for row in historical]
                    if (all(len(stamp) == 14 and stamp.isdigit() for stamp in stamps)
                            and stamps == sorted(set(stamps))
                            and stamps[-1][:8] < now.strftime("%Y%m%d")
                            and display == historical[-len(display):]):
                        return view
        if (timeframe_key in _LIVE_MINUTE_TIMEFRAMES - {"M1"}
                and isinstance(projected, dict)
                and projected.get("projection") == "COMPLETED_TIMEFRAME"
                and isinstance(as_of, datetime)
                and isinstance(view, dict)
                and source_pool.get("market_source") == "INTEGRATED"
                and view.get("market_source") == "INTEGRATED"
                and source_pool.get("market_data_identity") ==
                    f"{session.request.stock.code}_AL"
                and view.get("market_data_identity") ==
                    source_pool.get("market_data_identity")
                and source_pool.get("timeframe_key") == timeframe_key
                and view.get("timeframe_key") == timeframe_key
                and source_pool.get("timeframe_minutes") == session.request.timeframe_minutes
                and view.get("timeframe_minutes") == session.request.timeframe_minutes
                and view.get("source_coverage_sufficient") is not False
                and view.get("display_only_integrated") is not True):
            from gui_market_data_host import MarketDataHost

            now = self._now_factory()
            now = (now.replace(tzinfo=SEOUL_TIMEZONE) if now.tzinfo is None
                   else now.astimezone(SEOUL_TIMEZONE))
            if (now.weekday() >= 5 or MarketDataHost._display_session_for_time(now)
                    not in {"NXT_PRE", "KRX", "NXT_AFTER"}):
                historical = view.get("candles")
                display = view.get("display_candles", historical)
                if (isinstance(historical, list) and historical
                        and isinstance(display, list) and display
                        and all(isinstance(row, dict) and row.get("is_complete") is not False
                                for row in historical)
                        and all(isinstance(row, dict) and row.get("is_complete") is not False
                                for row in display)):
                    stamps = [str(row.get("time") or "") for row in historical]
                    if (all(len(stamp) == 14 and stamp.isdigit() for stamp in stamps)
                            and stamps == sorted(set(stamps))
                            and stamps[-1][:8] == as_of.strftime("%Y%m%d")
                            and as_of.date() <= now.date()
                            and display == historical[-len(display):]):
                        if (expected_calculation is not None
                                and (not isinstance(expected_calculation, list)
                                     or not expected_calculation
                                     or expected_calculation != historical[-len(expected_calculation):])):
                            raise ValueError("SOR_M1_BLOCKED:DISPLAYED_CALCULATION_CHANGED")
                        return view
        if (live_higher_forming and timeframe_key == "M3"
                and 15 * 60 + 40 <= as_of.hour * 60 + as_of.minute < 20 * 60
                and not self._sor_m3_after_anchor_verified(source_pool, as_of)):
            raise ValueError("SOR_M1_BLOCKED:BROKER_AFTER_ANCHOR_UNVERIFIED")
        if (live_higher_forming and timeframe_key == "M15"
                and 15 * 60 + 40 <= as_of.hour * 60 + as_of.minute < 20 * 60
                and not self._sor_m15_after_anchor_verified(source_pool, as_of)):
            raise ValueError("SOR_M1_BLOCKED:BROKER_AFTER_ANCHOR_UNVERIFIED")
        if (d1 or period) and isinstance(as_of, datetime) and isinstance(view, dict):
            if d1 and self._fresh_selected_d1_view(
                    session, source_pool, view, as_of, expected_calculation):
                return view
            historical = view.get("candles")
            display = view.get("display_candles", historical)
            if (isinstance(historical, list) and historical
                    and isinstance(display, list) and display
                    and all(isinstance(row, dict) for row in historical)
                    and all(isinstance(row, dict) for row in display)):
                stamps = [str(row.get("time") or "") for row in historical]
                historical_period_view = False
                if period:
                    try:
                        last_period_date = datetime.strptime(stamps[-1], "%Y%m%d%H%M%S").date()
                    except ValueError:
                        raise ValueError("SOR_M1_BLOCKED:PERIOD_IDENTITY_INVALID") from None
                    if last_period_date > as_of.date():
                        raise ValueError("SOR_M1_BLOCKED:PERIOD_IDENTITY_INVALID")
                    historical_period_view = (
                        (timeframe_key == "W1" and
                         last_period_date.isocalendar()[:2] != as_of.date().isocalendar()[:2])
                        or (timeframe_key == "MO1" and
                            (last_period_date.year, last_period_date.month)
                            != (as_of.year, as_of.month))
                        or (timeframe_key == "Y1" and last_period_date.year != as_of.year)
                    )
                if (stamps == sorted(set(stamps))
                        and (historical_period_view or
                             (d1 and stamps[-1][:8] < as_of.strftime("%Y%m%d")))):
                    if (display != historical[-len(display):]
                            or (expected_calculation is not None
                                and (not isinstance(expected_calculation, list)
                                     or not expected_calculation
                                     or expected_calculation != historical[-len(expected_calculation):]))):
                        raise ValueError("SOR_M1_BLOCKED:DISPLAYED_CALCULATION_CHANGED")
                    return view
        if (timeframe_key == "M1" and session.request.stock.code == "005930" and not forming
                and isinstance(projected, dict)
                and projected.get("availability_state") == "SOR_M1_SOURCE_UNAVAILABLE"):
            direct_active = self._fresh_selected_m1_view(
                session, source_pool, view, as_of, expected_calculation)
            if direct_active is not None:
                return direct_active
        if (timeframe_key == "M1" and session.request.stock.code == "005930" and not forming
                and isinstance(projected, dict)
                and projected.get("availability_state") == "SOR_M1_SOURCE_UNAVAILABLE"):
            from gui_market_data_host import MarketDataHost

            now = self._now_factory()
            now = (now.replace(tzinfo=SEOUL_TIMEZONE) if now.tzinfo is None
                   else now.astimezone(SEOUL_TIMEZONE))
            query = source_pool.get("direct_selected_query")
            rows = source_pool.get("candles")
            historical = view.get("candles") if isinstance(view, dict) else None
            display = view.get("display_candles", historical) if isinstance(view, dict) else None
            selected_at = (self._source_snapshot_datetime(query.get("request_as_of"))
                           if isinstance(query, dict) else None)
            if (now.date() == as_of.date()
                    and (now.weekday() >= 5 or MarketDataHost._display_session_for_time(now)
                         not in {"NXT_PRE", "KRX", "NXT_AFTER"})
                    and (as_of.weekday() >= 5 or MarketDataHost._display_session_for_time(as_of)
                         not in {"NXT_PRE", "KRX", "NXT_AFTER"})
                    and isinstance(query, dict) and selected_at is not None
                    and selected_at.date() == as_of.date() and selected_at <= as_of
                    and isinstance(rows, list) and rows
                    and source_pool.get("request_id") == query.get("request_id")
                    and source_pool.get("selected_direct_forming_snapshot") is None
                    and self._direct_selected_query_valid(
                        query, session.request.stock, "M1", 1,
                        f"{session.request.stock.code}_AL", rows)
                    and query.get("source_content_sha256") == hashlib.sha256(json.dumps(
                        rows, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":"), allow_nan=False,
                    ).encode("utf-8")).hexdigest()
                    and isinstance(view, dict)
                    and source_pool.get("market_source") == view.get("market_source") == "INTEGRATED"
                    and source_pool.get("market_data_identity") == view.get("market_data_identity")
                        == f"{session.request.stock.code}_AL"
                    and source_pool.get("timeframe_key") == view.get("timeframe_key") == "M1"
                    and source_pool.get("timeframe_minutes") == view.get("timeframe_minutes") == 1
                    and view.get("source_coverage_sufficient") is not False
                    and view.get("display_only_integrated") is not True
                    and isinstance(historical, list) and historical
                    and isinstance(display, list) and display
                    and historical == rows[-len(historical):]
                    and display == historical[-len(display):]
                    and (expected_calculation is None or
                         (isinstance(expected_calculation, list) and expected_calculation
                          and expected_calculation == historical[-len(expected_calculation):]))):
                stamps = [str(row.get("time") or "") for row in rows
                          if isinstance(row, dict)]
                if (len(stamps) == len(rows) and stamps == sorted(set(stamps))
                        and all(len(stamp) == 14 and stamp.isdigit() for stamp in stamps)
                        and all(row.get("is_complete") is not False
                                and row.get("market_data_identity", f"{session.request.stock.code}_AL")
                                    == f"{session.request.stock.code}_AL"
                                and row.get("market_source", "INTEGRATED") == "INTEGRATED"
                                and row.get("timeframe_minutes", 1) == 1
                                and (end := self._direct_selected_completion_time(row, 1)) is not None
                                and end <= selected_at
                                and (values := tuple(row.get(field) for field in
                                    ("open", "high", "low", "close", "volume")))
                                and all(type(value) in (int, float) and math.isfinite(value)
                                        for value in values)
                                and values[2] > 0 and values[4] >= 0
                                and values[2] <= values[0] <= values[1]
                                and values[2] <= values[3] <= values[1]
                                for row in rows)):
                    return view
        code = session.request.stock.code
        if (not isinstance(projected, dict)
                or projected.get("available") is not True
                or (not forming and projected.get("complete") is not True)
                or (not forming and projected.get("completeness", {}).get("completed_only") is not True)
                or (forming and (projected.get("projection") != "FORMING_BASE_BAR"
                            or projected.get("complete") is not False
                            or projected.get("forming_included") is not True
                            or projected.get("completeness", {}).get("forming_included") is not True
                            or projected.get("forming_snapshot_source")
                            not in _V5_SOR_FORMING_SOURCES))
                or projected.get("calculation_only") is not True
                or projected.get("calculation_source") != "BROKER_AL_CONFIRMED_M1"
                or projected.get("source_proof")
                != "BROKER_ROWS_NOT_INDEPENDENT_MERGE_ATTESTATION"
                or projected.get("market_source") != "INTEGRATED"
                or projected.get("market_data_identity") != f"{code}_AL"
                or projected.get("timeframe_minutes") != (
                    1 if d1 or period else session.request.timeframe_minutes)
                or (not forming and projected.get("freshness", {}).get("as_of")
                    != as_of.isoformat(timespec="seconds"))):
            state = (projected.get("availability_state")
                     if isinstance(projected, dict) else "INVALID_PROJECTION")
            message = f"SOR_M1_BLOCKED:{state or 'INCOMPLETE_PROJECTION'}"
            detail = projected.get("gap_detail") if isinstance(projected, dict) else None
            if (state == "SOR_M1_INTERNAL_COVERAGE_GAP" and isinstance(detail, dict)
                    and all(key in detail for key in (
                        "stock_code", "session", "previous_available",
                        "next_available", "missing_from", "missing_through",
                        "missing_minute_slots",
                    ))):
                message += (
                    f" [stock={detail['stock_code']}, session={detail['session']}, "
                    f"between={detail['previous_available']}..{detail['next_available']}, "
                    f"missing={detail['missing_from']}..{detail['missing_through']}, "
                    f"slots={detail['missing_minute_slots']}]"
                )
            raise ValueError(message)
        source = view.get("candles")
        display = view.get("display_candles", source)
        candidates = projected.get("candles")
        if (not isinstance(source, list) or not source
                or not isinstance(display, list) or not display
                or not isinstance(candidates, list) or not candidates
                or view.get("source_coverage_sufficient") is False
                or view.get("display_only_integrated") is True):
            raise ValueError("SOR_M1_BLOCKED:CALCULATION_VIEW_UNALIGNED")
        converted = []
        for index, row in enumerate(candidates):
            stamp = candle_market_datetime(row)
            if (not isinstance(row, dict)
                    or (row.get("is_complete") is not True
                        and not (forming and index == len(candidates) - 1
                                 and row.get("is_complete") is False))
                    or stamp is None):
                raise ValueError("SOR_M1_BLOCKED:PROJECTED_BAR_INVALID")
            converted.append({
                "time": stamp.strftime("%Y%m%d%H%M%S"),
                **{field: row[field] for field in ("open", "high", "low", "close", "volume")},
            })
        source_times = [str(row.get("time") or "") for row in source]
        display_times = [str(row.get("time") or "") for row in display]
        candidate_times = [row["time"] for row in converted]
        today = as_of.strftime("%Y%m%d")
        def trace_coverage_mismatch(branch: str) -> None:
            try:
                from gui_market_data_host import _trace_sor_block

                owner = self.parent()
                getter = getattr(owner, "existing_market_data_host", None)
                market = getter() if callable(getter) else None
                cutoff = as_of.replace(second=0, microsecond=0)
                _trace_sor_block(
                    self, market, stock=code,
                    reason="TODAY_TARGET_COVERAGE_MISMATCH",
                    as_of=as_of, cutoff=cutoff,
                    expected_latest=cutoff - timedelta(minutes=1),
                    source_starts=source_times,
                    candidate_starts=candidate_times,
                    target_starts=projected.get("today_target_starts") or [],
                    stage="VIEW", branch=branch,
                    timeframe_minutes=session.request.timeframe_minutes,
                )
            except Exception:
                pass
        if d1 or period:
            cutoff = as_of.replace(second=0, microsecond=0)
            try:
                received = datetime.fromisoformat(
                    projected["forming_snapshot_received_at"]
                )
                fresh = datetime.fromisoformat(projected["freshness"]["as_of"])
            except (KeyError, TypeError, ValueError):
                raise ValueError("SOR_M1_BLOCKED:D1_FORMING_TIME_INVALID") from None
            if (received.utcoffset() != timedelta(hours=9)
                    or fresh != received.replace(microsecond=0)
                    or not cutoff <= received <= as_of
                    or candidates[-1].get("is_complete") is not False
                    or candidate_times[-1] != cutoff.strftime("%Y%m%d%H%M%S")
                    or source_times != sorted(set(source_times))
                    or candidate_times != sorted(set(candidate_times))
                    or display_times != source_times[-len(display_times):]
                    or (d1 and source_times[-1] != f"{today}000000")
                    or any(stamp >= today for stamp in source_times[:-1])):
                raise ValueError("SOR_M1_BLOCKED:D1_SOURCE_OR_FORMING_INVALID")
            if period:
                try:
                    direct_date = datetime.strptime(source_times[-1], "%Y%m%d%H%M%S").date()
                except ValueError:
                    raise ValueError("SOR_M1_BLOCKED:PERIOD_IDENTITY_INVALID") from None
                if (source_times[-1][8:] != "000000" or direct_date > as_of.date()
                        or (timeframe_key == "W1" and
                            direct_date.isocalendar()[:2] != as_of.date().isocalendar()[:2])
                        or (timeframe_key == "MO1" and
                            (direct_date.year, direct_date.month) != (as_of.year, as_of.month))
                        or (timeframe_key == "Y1" and direct_date.year != as_of.year)):
                    raise ValueError("SOR_M1_BLOCKED:PERIOD_IDENTITY_INVALID")
            today_rows = [row for row in converted if row["time"].startswith(today)]
            expected_times = []
            minute = cutoff.replace(hour=8, minute=0)
            # A direct current row proves its date, not an earlier OHLCV cutoff.
            while minute <= cutoff:
                stamp = minute.strftime("%Y%m%d%H%M%S")
                if self._integrated_display_time_supported({"time": stamp}):
                    expected_times.append(stamp)
                minute += timedelta(minutes=1)
            if (not expected_times
                    or [row["time"] for row in today_rows] != expected_times):
                raise ValueError("SOR_M1_BLOCKED:D1_M1_COVERAGE_INCOMPLETE")
            for row in today_rows:
                values = tuple(row[field] for field in ("open", "high", "low", "close", "volume"))
                if (any(type(value) not in (int, float) or not math.isfinite(value)
                        for value in values)
                        or values[2] <= 0 or values[4] < 0
                        or not values[2] <= values[0] <= values[1]
                        or not values[2] <= values[3] <= values[1]):
                    raise ValueError("SOR_M1_BLOCKED:D1_M1_OHLCV_INVALID")
            previous = None
            if period and direct_date != as_of.date():
                previous = self._sor_period_prior_close(
                    code, timeframe_key, source_times[-1], as_of,
                )
            calculation = [*source[:-1], {
                "time": source_times[-1],
                "open": previous["open"] if previous else today_rows[0]["open"],
                "high": max(([previous["high"]] if previous else []) +
                            [row["high"] for row in today_rows]),
                "low": min(([previous["low"]] if previous else []) +
                           [row["low"] for row in today_rows]),
                "close": today_rows[-1]["close"],
                "volume": (previous["volume"] if previous else 0) +
                          sum(row["volume"] for row in today_rows),
            }]
        else:
            today_candidates = {
                row["time"]: row for row in converted
                if row["time"].startswith(today)
            }
            first_today_candidate = min(today_candidates) if today_candidates else ""
            if (candidate_times != sorted(set(candidate_times))
                    or source_times != sorted(set(source_times))
                    or display_times != source_times[-len(display_times):]):
                raise ValueError("SOR_M1_BLOCKED:TARGET_TIMESTAMP_COVERAGE_MISMATCH")
            today_source_times = [stamp for stamp in source_times if stamp.startswith(today)]
            if not today_source_times and source_times[-1][:8] < today and not live_higher_forming:
                if (expected_calculation is not None
                        and (not isinstance(expected_calculation, list)
                             or not expected_calculation
                             or expected_calculation != source[-len(expected_calculation):])):
                    raise ValueError("SOR_M1_BLOCKED:DISPLAYED_CALCULATION_CHANGED")
                return view
            if ((not today_source_times and source_times[-1][:8] >= today)
                    or source_times[-1][:8] > today
                    or (today_source_times and not today_candidates)
                    or any(stamp >= first_today_candidate and stamp not in today_candidates
                           for stamp in today_source_times)
                    or (today_source_times and today_source_times[-1] > candidate_times[-1])
                    or any(stamp > as_of.strftime("%Y%m%d%H%M%S")
                            for stamp in today_source_times)):
                trace_coverage_mismatch("SOURCE_TARGET")
                raise ValueError("SOR_M1_BLOCKED:TODAY_TARGET_COVERAGE_MISMATCH")
            calculation = ([row for stamp, row in zip(source_times, source)
                            if stamp < first_today_candidate] +
                           [row for row in converted if row["time"].startswith(today)]
                           if live_higher_forming else [
                                today_candidates.get(stamp, row)
                               for stamp, row in zip(source_times, source)
                           ])
            verified_no_trade = projected.get("verified_no_trade_minutes", ())
            if timeframe_key == "M1" and verified_no_trade:
                try:
                    derived_times = [datetime.fromisoformat(stamp).strftime("%Y%m%d%H%M%S")
                                     for stamp in verified_no_trade]
                except (TypeError, ValueError):
                    raise ValueError("SOR_M1_BLOCKED:NO_TRADE_PROOF_INVALID") from None
                latest_plus_one = (datetime.strptime(today_source_times[-1], "%Y%m%d%H%M%S")
                                   + timedelta(minutes=1)).strftime("%Y%m%d%H%M%S") if today_source_times else ""
                if (not today_source_times
                        or any(stamp not in today_candidates
                               or not today_source_times[0] < stamp <= latest_plus_one
                               or today_candidates[stamp]["volume"] != 0
                               or len({today_candidates[stamp][field] for field in
                                       ("open", "high", "low", "close")}) != 1
                               for stamp in derived_times)
                        or any(row != today_candidates[row["time"]]
                               for row in source if row.get("time") in derived_times)):
                    raise ValueError("SOR_M1_BLOCKED:NO_TRADE_PROOF_INVALID")
                calculation = ([row for row in source
                                if str(row.get("time") or "") < first_today_candidate] +
                               [today_candidates[stamp] for stamp in sorted(
                                    {stamp for stamp in today_source_times
                                     if stamp >= first_today_candidate} | set(derived_times))])
            if live_minute_forming:
                try:
                    received = datetime.fromisoformat(projected["forming_snapshot_received_at"])
                except (KeyError, TypeError, ValueError):
                    raise ValueError("SOR_M1_BLOCKED:FORMING_TIME_INVALID") from None
                target_start = candle_market_datetime(candidates[-1])
                source_minute = as_of.replace(second=0, microsecond=0)
                if (received.utcoffset() != timedelta(hours=9)
                        or not received <= as_of
                        or received.replace(second=0, microsecond=0) != as_of.replace(second=0, microsecond=0)
                        or candidates[-1].get("is_complete") is not False
                        or target_start is None
                        or not target_start <= source_minute < (
                            target_start + timedelta(minutes=session.request.timeframe_minutes))
                        or (live_higher_forming and
                            projected.get("forming_target_source_covered") is not True)
                        or source_times[-1] > candidate_times[-1]):
                    raise ValueError("SOR_M1_BLOCKED:FORMING_SOURCE_INVALID")
                if live_higher_forming:
                    if calculation[-1] != converted[-1]:
                        raise ValueError("SOR_M1_BLOCKED:FORMING_TARGET_MISMATCH")
                elif source_times[-1] < candidate_times[-1]:
                    calculation.append(converted[-1])
                else:
                    raise ValueError("SOR_M1_BLOCKED:FORMING_SOURCE_INVALID")
                if live_higher_forming:
                    if 15 * 60 + 40 <= source_minute.hour * 60 + source_minute.minute < 20 * 60:
                        direct = source_pool.get("candles")
                        direct_times = ([str(row.get("time") or "") for row in direct]
                                        if isinstance(direct, list) and all(
                                            isinstance(row, dict) for row in direct) else [])
                        prior = [stamp for stamp in direct_times if stamp[:8] < today]
                        anchor_day = max((stamp[:8] for stamp in prior), default="")
                        after = [stamp for stamp in prior if stamp[8:12] >= "1530"]
                        anchor_minute = (939 if timeframe_key == "M3" else
                                         930 if timeframe_key == "M15" else 940)
                        anchor_stamp = f"{anchor_day}{anchor_minute // 60:02d}{anchor_minute % 60:02d}00"
                        if (timeframe_key not in {"M3", "M5", "M10", "M15", "M30", "M60", "M120", "M240"}
                                or not anchor_day
                                or anchor_stamp not in after
                                or any(len(stamp) != 14 or not stamp.isdigit()
                                       for stamp in direct_times)
                                or any(stamp[8:12] < anchor_stamp[8:12] or stamp[8:12] >= "2000" or
                                       (int(stamp[8:10]) * 60 + int(stamp[10:12]) - anchor_minute)
                                       % session.request.timeframe_minutes
                                       for stamp in after)):
                            raise ValueError("SOR_M1_BLOCKED:BROKER_AFTER_ANCHOR_UNVERIFIED")
                    # Direct M120/M240 broker anchors can differ from the M1 projector.
                    if timeframe_key in {"M120", "M240"}:
                        regular = [stamp for stamp in source_times if
                                   "0900" <= stamp[8:12] < "1530"]
                        prior_days = sorted({stamp[:8] for stamp in regular
                                             if stamp[:8] < today})
                        anchor_day = prior_days[-1] if prior_days else ""
                        anchor_times = {stamp[8:12] for stamp in regular
                                        if stamp[:8] == anchor_day}
                        second = f"{9 + session.request.timeframe_minutes // 60:02d}00"
                        if (not {"0900", second}.issubset(anchor_times) or any(
                                (int(stamp[8:10]) * 60 + int(stamp[10:12]) - 540)
                                % session.request.timeframe_minutes for stamp in regular)):
                            raise ValueError("SOR_M1_BLOCKED:BROKER_TARGET_ANCHOR_UNVERIFIED")
                    if (source_times[-1] == candidate_times[-1]
                            and source_times[-1] not in today_source_times):
                        raise ValueError("SOR_M1_BLOCKED:FORMING_TARGET_MISMATCH")
                    if projected.get("today_m1_source_covered") is not True:
                        raise ValueError("SOR_M1_BLOCKED:TODAY_M1_COVERAGE_INCOMPLETE")
                    if ([stamp for stamp in candidate_times if stamp.startswith(today)]
                            != projected.get("today_target_starts")):
                        trace_coverage_mismatch("PROJECTED_TARGET")
                        raise ValueError("SOR_M1_BLOCKED:TODAY_TARGET_COVERAGE_MISMATCH")
        if (expected_calculation is not None
                and (not isinstance(expected_calculation, list)
                     or not expected_calculation
                     or expected_calculation != calculation[-len(expected_calculation):])):
            raise ValueError("SOR_M1_BLOCKED:DISPLAYED_CALCULATION_CHANGED")
        display_calculation = (calculation[-min(5000, max(
            len(display) + len(projected.get("verified_no_trade_minutes", ())),
            len(projected.get("today_target_starts", ()))
        )):] if live_minute_forming or (
            timeframe_key == "M1" and projected.get("verified_no_trade_minutes"))
                               else list(display))
        settings = getattr(session.request, "settings_snapshot", None)
        rules = settings.to_dict() if callable(getattr(settings, "to_dict", None)) else {}
        scope = rules.get("validation_market_scope")
        if (isinstance(scope, dict) and scope.get("regular_market_only") is True
                and timeframe_key in _LIVE_MINUTE_TIMEFRAMES):
            display_calculation = _krx_regular_session_candles(display_calculation)
        if live_calendar and (d1 or period):
            if (not display_calculation or display_calculation[-1]["time"]
                    != calculation[-1]["time"]):
                raise ValueError("SOR_M1_BLOCKED:LIVE_CALENDAR_DISPLAY_IDENTITY_MISMATCH")
            display_calculation[-1] = dict(calculation[-1])
        result = dict(view, candles=calculation, display_candles=display_calculation,
                      calculation_source=(
                          "SELECTED_DIRECT_AL_PERIOD_WITH_BROKER_AL_M1_TODAY"
                          if period else ("SELECTED_DIRECT_AL_D1_WITH_BROKER_AL_M1_TODAY"
                                          if d1 else "SELECTED_HISTORY_WITH_BROKER_AL_CONFIRMED_M1_TODAY")
                      ),
                      source_proof="BROKER_ROWS_NOT_INDEPENDENT_MERGE_ATTESTATION",
                      sor_m1_calculation_complete=True,
                      source_coverage_sufficient=view.get("source_coverage_sufficient"),
                      production_calculation_cache_identity=(
                          "SOR_M1", projected.get("source_identity"), as_of.isoformat()))
        receipt = projected.get("sor_source_receipt")
        if (isinstance(receipt, dict)
                and receipt.get("stock_code") == code
                and receipt.get("source_as_of") == as_of.isoformat()
                and receipt.get("source_base_timeframe_key") == "M1"
                and receipt.get("timeframe_key") ==
                    f"M{projected['timeframe_minutes']}"):
            result["sor_source_receipt"] = deepcopy(receipt)
            if d1 or period:
                result["sor_source_receipt"]["timeframe_key"] = timeframe_key
                result["sor_source_receipt"]["target_timeframe_key"] = timeframe_key
        result["snapshot"] = ValidationHistoricalSnapshot(
            stock=session.request.stock,
            timeframe_minutes=session.request.timeframe_minutes,
            timeframe_key=session.request.timeframe_key,
            requested_count=len(calculation),
            request_id=f"{view.get('request_id') or 'POOL'}:SOR_M1_CALCULATION",
            rows=self._historical_rows_from_candles(calculation),
            market_data_identity=str(view["market_data_identity"]),
            market_source=str(view["market_source"]),
            request_as_of=(projected["forming_snapshot_received_at"]
                           if live_higher_forming else as_of.isoformat()),
        )
        return result

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
        request["sor_m1_calculation_snapshot"] = self._sor_m1_calculation_snapshot(
            session, pool, live_forming=request.get("live_m1_snapshot_key") is not None,
        )
        frozen = request["sor_m1_calculation_snapshot"]
        proof = frozen[2] if isinstance(frozen, tuple) and len(frozen) == 3 else None
        if (isinstance(proof, dict)
                and proof.get("availability_state") in
                    {"SOR_M1_SOURCE_UNAVAILABLE", "SOR_DIRECT_HISTORY_UNAVAILABLE"}
                and session.request.timeframe_key in _LIVE_MINUTE_TIMEFRAMES - {"M1"}):
            request["normal_base_only"] = True
        if getattr(window, "_normal_base_requested", False) or request.get("normal_base_only"):
            self._start_normal_base_prepare(request, history_target)
            return
        settings_hash = session.request.settings_snapshot.rules_hash
        cached_by_settings = pool.setdefault(
            "signal_entries_by_settings_hash",
            {},
        )
        cached_signatures = pool.setdefault(
            "signal_entries_signature_by_settings_hash",
            {},
        )
        cached_cost_scan = pool.get("trading_cost_signal_entries_cache")
        reusable_cost_replays = (
            dict(pool.get("trading_cost_replay_cache"))
            if isinstance(pool.get("trading_cost_replay_cache"), dict)
            else {}
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
        payload_base = {}
        projection_as_of = self._now_factory()
        target_count = history_target + required_validation_history_context_bars(
            session.request.settings_snapshot.to_dict()
        )

        def prepare_view() -> tuple[dict[str, object], object, object, object, object]:
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
                and self._verified_sor_tail_provider is None
                and request.get("sor_m1_calculation_snapshot") is None
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
            view_pool = self._verified_sor_view_for_session(
                session, worker_pool, view_pool, as_of,
                dispatch_source_identity,
            )
            marking_source_view = view_pool
            view_pool = self._sor_m1_calculation_view(
                session, worker_pool, view_pool,
                request.get("sor_m1_calculation_snapshot"),
            )
            frozen = request.get("sor_m1_calculation_snapshot")
            if (session.request.timeframe_key == "M1"
                    and isinstance(frozen, tuple) and len(frozen) == 3
                    and isinstance(frozen[2], dict)
                    and frozen[2].get("projection") == "FORMING_BASE_BAR"
                    and view_pool.get("sor_m1_calculation_complete") is True):
                payload_base["m1_marking_basis"] = (marking_source_view, frozen)
            historical_snapshot = view_pool.get("snapshot")
            active_signal_signature = self._signal_view_signature(
                view_pool, source_identity=dispatch_source_identity,
            )
            signal_entries = (
                cached_by_settings.get(settings_hash)
                if (
                    isinstance(cached_by_settings, dict)
                    and isinstance(cached_signatures, dict)
                    and cached_signatures.get(settings_hash) == active_signal_signature
                )
                else None
            )
            cost_prefix = self._trading_cost_reuse_prefix(session, view_pool)
            cost_scan_key = (
                cost_prefix + (active_signal_signature,)
                if cost_prefix is not None and isinstance(active_signal_signature, tuple)
                else None
            )
            if (
                signal_entries is None
                and cost_scan_key is not None
                and isinstance(cached_cost_scan, tuple)
                and len(cached_cost_scan) == 3
                and cached_cost_scan[0] == cost_scan_key
                and cached_cost_scan[1] != settings_hash
            ):
                signal_entries = cached_cost_scan[2]
            return (view_pool, historical_snapshot, active_signal_signature,
                    tuple(signal_entries) if signal_entries is not None else None,
                    cost_scan_key)

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

        payload_base.update({
            "window_key": window_key,
            "generation": generation,
            "prepare_token": token,
            "session": session,
            "pool": pool,
            "evaluation_count": evaluation_count,
            "settings_hash": settings_hash,
            "history_target": history_target,
        })
        self._prepare_active[window_key] = token

        @_perf_stage("worker.prepare_result", owner=window)
        def prepare_result() -> None:
            trace_timing = signal_validation_trace_enabled()
            prepare_started = time.perf_counter() if trace_timing else 0.0
            phase_ms: dict[str, float] = {}
            trace_view_candle_count = 0
            scan_reused = False
            full_replay_reused = False
            presentation_reused = False

            def phase_start() -> float:
                return time.perf_counter() if (trace_timing or _PERF_ENABLED) else 0.0

            def phase_end(name: str, started: float) -> None:
                if _PERF_ENABLED:
                    _perf_emit("prepare.phase." + name, started, window, bars=trace_view_candle_count)
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
                 cached_entries, cost_scan_key) = (
                    legacy_view if legacy_view is not None else prepare_view()
                )
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
                payload_base["trading_cost_signal_entries_key"] = cost_scan_key
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
                            **({"projected_candles_override": view_pool["candles"]}
                               if (view_pool.get("native_forming_included") is True
                                   or (view_pool.get("timeframe_key") == "M1"
                                       and view_pool.get("calculation_source")
                                       == "SELECTED_DIRECT_AL_WITH_DIRECT_CURRENT"
                                       and view_pool.get("sor_m1_calculation_complete") is True))
                               else {}),
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
                    reusable_cost_replays=reusable_cost_replays,
                    sor_m1_calculation_snapshot=request.get("sor_m1_calculation_snapshot"),
                )
                phase_end("detail_replay", detail_started)
                replay_snapshot = replay_result.snapshot
                payload_base["trading_cost_replay_key"] = (
                    self._trading_cost_replay_key(
                        session,
                        view_pool,
                        replay_snapshot.to_candles(),
                        replay_snapshot.evaluated_start_index,
                        replay_snapshot.evaluated_end_index,
                    )
                )
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
                if (session.request.timeframe_minutes in SUPPORTED_BAR_MINUTES
                        and session.request.timeframe_key == f"M{session.request.timeframe_minutes}"
                        and current()):
                    try:
                        marker_path = self._validation_cache_source_marker_path(
                            session.request.stock, session.request.timeframe_minutes,
                            session.request.timeframe_key,
                            int(pool.get("requested_count") or 0))
                        if (marker_path is not None and marker_path.is_file()
                                and marker_path.stat().st_size <= 4096):
                            source_marker = json.loads(marker_path.read_text(encoding="utf-8"))
                            evidence = build_v5_records(
                                pool, source_marker, replay_result.snapshot,
                                replay_result.snapshot.to_candles(),
                                replay_result.snapshot.to_entries(),
                                generation=generation, request_generation=generation,
                                window_key=window_key, capture_kind="NORMAL_REPLAY")
                            if evidence:
                                payload["sor_m3_evidence_lines"] = "".join(
                                    json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n"
                                    for record in evidence)
                                payload["sor_evidence_timeframe"] = session.request.timeframe_key
                    except (OSError, ValueError, TypeError, AttributeError):
                        pass  # Optional evidence cannot interrupt replay.
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
        def _observe(outcome: str, *, window: object = None,
                     status_attempted: bool = False) -> None:
            session = payload.get("session")
            if (not _m3_observation.ENABLED or payload.get("normal_base") is not True
                    or payload.get("m3_observation_selected") is not True
                    or getattr(getattr(session, "request", None), "timeframe_key", None) != "M3"
                    or getattr(getattr(getattr(session, "request", None), "stock", None),
                               "code", None) != "005930"):
                return
            pool = payload.get("pool")
            pool = pool if isinstance(pool, dict) else {}
            proof = pool.get("native_live_snapshot")
            proof = proof if isinstance(proof, dict) else {}
            observed_view = prepared_view_pool if isinstance(prepared_view_pool, dict) else {}
            display = getattr(window, "_candles", None) if window is not None else None
            calculation = (getattr(window, "_calculation_candles", None)
                           if window is not None else None)
            label = getattr(window, "validation_status_label", None)
            _m3_observation.record(
                "publication", stock_code="005930", timeframe="M3",
                job=payload.get("m3_observation_job"),
                window_key=window_key, generation=generation,
                request_id=proof.get("request_id") or payload.get("live_native_request_id"),
                tick_revision=proof.get("tick_revision"), outcome=outcome,
                signal=payload.get("live_native_signal") or payload.get("live_sor_signal"),
                source=observed_view.get("market_source"),
                identity=observed_view.get("market_data_identity"),
                current_snapshot_source=(proof.get("forming_candle", {}).get("snapshot_source")
                                         if isinstance(proof.get("forming_candle"), dict) else None),
                native_forming_included=observed_view.get("native_forming_included"),
                forming_status=observed_view.get("forming_bar_status"),
                display_count=len(display) if isinstance(display, list) else None,
                display_tail=_m3_observation.tail(display),
                calculation_count=len(calculation) if isinstance(calculation, list) else None,
                calculation_tail=_m3_observation.tail(calculation),
                status_publish_attempted=status_attempted,
                status_label_after=(label.text() if callable(getattr(label, "text", None))
                                    else None))
        def observe(outcome: str, *, window: object = None,
                    status_attempted: bool = False) -> None:
            try:
                _observe(outcome, window=window, status_attempted=status_attempted)
            except Exception:
                pass  # A diagnostic cannot change publication or rejection.
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
            observe("inactive_job")
            return
        pending = self._prepare_pending.pop(window_key, None)
        if pending is not None:
            observe("pending_replacement")
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
            observe("cancelled_or_generation_changed")
            if (
                payload.get("cancelled") is True
                and self._request_generation.get(window_key) == generation
            ):
                cancelled_window = self._open_windows.get(window_key)
                restore_stale = getattr(
                    cancelled_window,
                    "restore_stale_validation_view",
                    None,
                )
                if callable(restore_stale):
                    restore_stale()
            self._clear_window_candle_preview(window_key, token)
            self._prepare_active.pop(window_key, None)
            self._range_replay_pending.pop(window_key, None)
            return
        window = self._open_windows.get(window_key)
        if window is None:
            observe("window_closed")
            self._prepare_active.pop(window_key, None)
            self._range_replay_pending.pop(window_key, None)
            return
        if not self._window_accepts_results(window):
            observe("window_rejected", window=window)
            self._clear_window_candle_preview(window_key, token)
            self._prepare_active.pop(window_key, None)
            self._prepare_pending.pop(window_key, None)
            self._range_replay_pending.pop(window_key, None)
            self._complete_history_extension(window_key, success=False)
            return
        error = str(payload.get("error") or "").strip()
        if error:
            observe("prepare_error", window=window)
            self._prepare_active.pop(window_key, None)
            self._range_replay_pending.pop(window_key, None)
            if (error == "SOR_M1_BLOCKED:SOR_M1_SOURCE_UNAVAILABLE"
                    and self._resume_cold_m1_source(window_key)):
                return
            self._fail_window(window, f"SIGNAL_SCAN_ERROR: {error}")
            return
        session = payload.get("session")
        pool = payload.get("pool")
        if not isinstance(session, ValidationSession) or not isinstance(pool, dict):
            observe("invalid_prepare_result", window=window)
            self._prepare_active.pop(window_key, None)
            self._range_replay_pending.pop(window_key, None)
            self._fail_window(window, "SIGNAL_SCAN_ERROR: invalid prepare result")
            return
        if self._historical_pools.get(window_key) is not pool:
            observe("pool_replaced", window=window)
            self._clear_window_candle_preview(window_key, token)
            self._prepare_active.pop(window_key, None)
            self._range_replay_pending.pop(window_key, None)
            return
        if not self._retained_native_nx_publication_current(
                window, session, pool, generation):
            observe("retained_source_rejected", window=window)
            self._clear_window_candle_preview(window_key, token)
            self._prepare_active.pop(window_key, None)
            self._range_replay_pending.pop(window_key, None)
            return
        live_native_id = payload.get("live_native_request_id")
        if (live_native_id is not None
                and not self._live_native_snapshot_is_current(
                    window, session, pool, live_native_id, generation, prepared_view_pool)):
            observe("native_proof_mismatch", window=window)
            if (not self._native_selected_window_ready(window, window_key)
                    and self._validation_sessions.get(window_key) is session
                    and self._historical_pools.get(window_key) is pool
                    and self._request_generation.get(window_key) == generation
                    and window_key not in self._native_selected_pending):
                self._native_selected_pending[window_key] = (session, pool, generation)
            self._clear_window_candle_preview(window_key, token)
            self._prepare_active.pop(window_key, None)
            self._start_pending_window_work(window_key)
            return
        if (isinstance(prepared_view_pool, dict)
                and prepared_view_pool.get("validation_composite_calculation") is True
                and prepared_view_pool.get("_validation_nxt_pre_sidecar_identity")
                != self._nxt_pre_sidecar_identity(
                    self._integrated_display_sidecars.get(
                        self._integrated_display_sidecar_key(session)
                    )
                )):
            observe("sidecar_changed", window=window)
            self._clear_window_candle_preview(window_key, token)
            self._prepare_active.pop(window_key, None)
            self._range_replay_pending.pop(window_key, None)
            self._use_pool_for_window(
                window, session, pool,
                evaluation_count=int(payload.get("evaluation_count") or 100),
            )
            return
        if payload.get("normal_base") is True:
            interaction_epoch = self._window_busy_epoch(window)
            if interaction_epoch is not None:
                observe("interaction_deferred", window=window)
                self._prepare_deferred[window_key] = (interaction_epoch, dict(payload))
                return
            try:
                installed = self._apply_pool_to_window(
                    window, session, pool, evaluation_count=payload["evaluation_count"],
                    signal_entries=None, prepared_view_pool=payload["prepared_view_pool"],
                    normal_base_presentation=payload["normal_base_presentation"],
                    m1_marking_basis=payload.get("m1_marking_basis"))
                status_attempted = False
                if (installed and live_native_id is not None
                        and self._live_native_snapshot_is_current(
                            window, session, pool, live_native_id, generation,
                            prepared_view_pool)):
                    publish = getattr(window, "set_live_sor_monitor_signal", None)
                    if callable(publish):
                        status_attempted = True
                        publish(payload.get("live_native_signal") or {
                            "unavailable": True,
                            "stock_code": session.request.stock.code,
                            "timeframe_key": session.request.timeframe_key,
                        }, source_as_of=pool["native_live_snapshot"]["request_receipt"][1]["received_at"])
                live_key = payload.get("live_m1_snapshot_key")
                if installed and live_key is not None:
                    try:
                        if (self._prepare_active.get(window_key) is token
                                and self._request_generation.get(window_key) == generation
                                and window_key not in self._prepare_pending
                                and self._live_sor_snapshot_is_current(window, session, pool, live_key)):
                            publish = getattr(window, "set_live_sor_monitor_signal", None)
                            if callable(publish):
                                publish(payload.get("live_sor_signal") or {
                                    "unavailable": True,
                                    "stock_code": session.request.stock.code,
                                    "timeframe_key": session.request.timeframe_key,
                                },
                                        source_as_of=live_key[3])
                    except Exception:
                        pass  # Status is optional; a failed observer cannot undo the chart.
            except Exception as exc:
                observe("install_error", window=window)
                self._range_replay_pending.pop(window_key, None)
                self._fail_window(window, f"NORMAL_BASE_VIEW_ERROR: {exc}")
            else:
                observe("installed" if installed else "install_rejected", window=window,
                        status_attempted=status_attempted)
            finally:
                self._prepare_active.pop(window_key, None)
            self._start_pending_window_work(window_key)
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
        cost_scan_key = payload.get("trading_cost_signal_entries_key")
        if isinstance(cost_scan_key, tuple) and settings_hash:
            pool["trading_cost_signal_entries_cache"] = (
                cost_scan_key,
                settings_hash,
                optimized_entries,
            )
        self._publish_trading_cost_replay(
            pool,
            payload.get("trading_cost_replay_key"),
            payload.get("replay_snapshot"),
        )
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
                and not (isinstance(prepared_view_pool, dict)
                         and prepared_view_pool.get("sor_m1_calculation_complete") is True)
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
                m1_marking_basis=payload.get("m1_marking_basis"),
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
        self._append_accepted_m3_replay_evidence(payload)
        pending_range = self._range_replay_pending.pop(window_key, None)
        if pending_range is not None:
            self._start_range_replay(pending_range)
        else:
            self._start_pending_window_work(window_key)

    def _append_accepted_m3_replay_evidence(self, payload: dict[str, object]) -> None:
        key = payload.get("window_key")
        generation = payload.get("generation")
        snapshot = payload.get("replay_snapshot")
        lines = payload.get("sor_m3_evidence_lines")
        request_id = getattr(snapshot, "historical_request_id", None)
        if (not isinstance(lines, str) or not lines or not request_id
                or payload.get("normal_base") is True
                or self._request_generation.get(key) != generation
                or key in self._prepare_pending):
            return
        identity = (generation, request_id)
        if self._sor_m3_replay_published.get(key) != identity:
            timeframe = payload.get("sor_evidence_timeframe", "M3")
            queued = (_queue_v5_m3_evidence(lines) if timeframe == "M3" else
                      _queue_v5_m3_evidence(lines, timeframe=timeframe))
            if queued:
                self._sor_m3_replay_published[key] = identity

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
            (hashlib.sha256(json.dumps(
                pool["selected_direct_forming_snapshot"], ensure_ascii=False,
                sort_keys=True, separators=(",", ":"), default=str,
            ).encode("utf-8")).hexdigest()
             if isinstance(pool.get("selected_direct_forming_snapshot"), dict) else ""),
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
            eligibility = self._verified_nxt_validation_stock(stock)
            if type(eligibility) is not bool:
                self._validation_source_by_code.pop(stock.code, None)
                self._validation_frozen_krx_codes.discard(stock.code)
                self._invalidate_window_requests(window_key)
                self._fail_window(window, "NXT_ELIGIBILITY_NOT_VERIFIED")
                return
            now = self._now_factory().astimezone(SEOUL_TIMEZONE)
            self._validation_nxt_pre_codes.discard(stock.code)
            if now.hour == 8 and now.minute < 50:
                plan = read_stock_market_source_plan(PROJECT_ROOT, stock.code, now.date())
                selected = source_for_time(plan, now) if plan is not None else None
                if (selected is None or selected["source"] != "NXT"
                        or plan["stock_name"] != stock.name or eligibility is not True):
                    self._fail_window(window, "NXT_PRE_SOURCE_UNAVAILABLE")
                    return
                self._validation_nxt_pre_codes.add(stock.code)
            stock_dir = StockRepository(PROJECT_ROOT).resolve_stock_dir(
                stock.code, stock.name,
            )
            calculation_source = read_json_dict(stock_dir / "config.json").get(
                "calculation_market_source", "SOR"
            )
            if calculation_source not in ({"SOR"} if eligibility else {"SOR", "KRX"}):
                self._fail_window(window, "CALCULATION_MARKET_SOURCE_INVALID")
                return
            if not eligibility:
                plan = read_stock_market_source_plan(
                    PROJECT_ROOT, stock.code, datetime.now(SEOUL_TIMEZONE).date(),
                )
                if (plan is None or plan["nxt_available"] is not False
                        or plan["stock_name"] != stock.name):
                    self._fail_window(window, "SOR_SOURCE_UNAVAILABLE")
                    return
                self._validation_frozen_krx_codes.add(stock.code)
            else:
                self._validation_frozen_krx_codes.discard(stock.code)
            first_source_verification = self._validation_source_by_code.get(stock.code) is not eligibility
            self._validation_source_by_code[stock.code] = eligibility
            self._sync_v5_observation_lease(window)
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
                market_source_observation=self._market_source_observation(stock.code),
                sor_registration_observation=self._sor_registration_observation(
                    stock.code
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
            self._broker_al_m1_request_receipts.pop(window_key, None)
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

            @_perf_stage("worker.prepare_history_cache", owner=window)
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
                                source_verified_at=persistent_entry.updated_at,
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
        if isinstance(history_cache_bundle, dict):
            persistent = history_cache_bundle.get("persistent")
        elif force_historical_refresh:
            persistent = None
        else:
            cache_read_started = time.perf_counter() if _PERF_ENABLED else 0.0
            persistent = self._cached_history(
                stock,
                timeframe_minutes,
                timeframe_key,
                fetch_count,
            )
            if _PERF_ENABLED:
                _perf_emit(
                    "history.refresh.cache_ready",
                    cache_read_started,
                    window,
                    window_key=window_key,
                    generation=generation,
                    probe_count=min(_PERSISTENT_REFRESH_PROBE_COUNT, fetch_count),
                    cached_rows=(len(persistent.candles) if persistent is not None else 0),
                    fetch_count=fetch_count,
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
                source_verified_at=persistent.updated_at,
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
            requester = _V5SelectedSourceRequester(
                self._broker, stock, timeframe_key,
                self._validation_source_by_code[stock.code],
                on_verified_period_response=self._capture_sor_period_prior_close,
                now_factory=self._now_factory,
                on_verified_native_response=self._remember_native_receipt,
            )
            provider = self._historical_provider_factory(
                fetch_session, requester,
                **self._broker_al_m1_provider_options(fetch_session),
            )
        except Exception as exc:
            self._fail_window(window, f"HISTORICAL_PROVIDER_ERROR: {exc}")
            return

        request_latest = getattr(provider, "request_latest", None)
        if not callable(request_latest):
            self._fail_window(window, "HISTORICAL_PROVIDER_UNAVAILABLE")
            return

        def trace_historical_result_error(result: object) -> None:
            if stock.code != "000660" or timeframe_key != "M3":
                return
            try:
                source_snapshot = (
                    result.snapshot if isinstance(result, ValidationHistoricalResult)
                    else None
                )
                if (not isinstance(result, ValidationHistoricalResult)
                        or result.ok is not True or source_snapshot is None):
                    trace_reason = getattr(result, "reason", None) or "HISTORICAL_RESULT_FAILED"
                elif not self._historical_result_matches_validation_source(
                    result, fetch_session.request.stock,
                ):
                    trace_reason = "MARKET_SOURCE_IDENTITY_INVALID"
                else:
                    return
                _trace_m3_sor_historical_error(
                    stock=stock.code, timeframe=timeframe_key,
                    response={
                        "ok": getattr(result, "ok", False),
                        "market_source": getattr(source_snapshot, "market_source", ""),
                        "market_data_identity": getattr(
                            source_snapshot, "market_data_identity", "",
                        ),
                    },
                    started={"request_as_of": getattr(
                        source_snapshot, "request_as_of", "",
                    )},
                    reason=trace_reason,
                    error_class=(
                        "SOURCE_IDENTITY_MISMATCH"
                        if trace_reason == "MARKET_SOURCE_IDENTITY_INVALID"
                        else "BROKER_ERROR_TEXT" if getattr(result, "error", None)
                        else "HISTORICAL_RESULT_ERROR"
                    ),
                    code_path="historical_result",
                    snapshot_present=source_snapshot is not None,
                )
            except Exception:
                return  # Passive diagnostics cannot change historical handling.

        def request_full(request_count: int = fetch_count) -> None:
            target = current_target()
            if target is None:
                return
            self._active_providers[request_key] = provider

            def full_completed(result: object) -> None:
                trace_historical_result_error(result)
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
                    self._retain_broker_al_m1_result(window_key, result)

                    @_perf_stage("worker.prepare_history_result", owner=window)
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
                                candles, forming, direct_query = self._direct_selected_history_parts(
                                    result.snapshot)
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
                                    direct_selected_query=direct_query,
                                    selected_direct_forming_snapshot=forming,
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
                self._retain_broker_al_m1_result(window_key, result)
                if (
                    production_calculation_enabled
                    and self._historical_result_matches_validation_source(
                        result,
                        fetch_session.request.stock,
                    )
                ):
                    try:
                        source_candles, _, _ = self._direct_selected_history_parts(
                            result.snapshot)
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

        if (stock.code == "005930" and timeframe_key == "M1"
                and persistent.market_source == "INTEGRATED"):
            # Active M1 requires one fresh full selected response; a short cache
            # probe cannot bind its completed 5,900 rows to a forming receipt.
            request_full()
            return

        cached_latest_time = str(persistent.candles[-1]["time"])

        def request_refresh(probe_count: int) -> None:
            target = current_target()
            if target is None:
                return
            count = min(fetch_count, max(1, int(probe_count)))
            attempt_owner = object() if _PERF_ENABLED else None
            attempt_started = 0.0
            self._active_providers[request_key] = provider

            def refresh_completed(result: object) -> None:
                trace_historical_result_error(result)
                if _PERF_ENABLED:
                    _perf_emit(
                        "history.refresh.response_received",
                        attempt_started,
                        attempt_owner,
                        window_key=window_key,
                        generation=generation,
                        probe_count=count,
                        response_ok=(getattr(result, "ok", None) is True),
                    )
                    callback_started = time.perf_counter()
                self._active_providers.pop(request_key, None)
                refresh_target = current_target()
                if refresh_target is None:
                    return
                if (not isinstance(result, ValidationHistoricalResult)
                        or result.ok is not True or result.snapshot is None):
                    self._fail_window(refresh_target, self._failure_text("HISTORICAL", result))
                    return
                if not self._historical_result_matches_validation_source(
                    result,
                    fetch_session.request.stock,
                ):
                    self._fail_window(refresh_target, "HISTORICAL: MARKET_SOURCE_IDENTITY_INVALID")
                    return
                try:
                    incoming, forming, direct_query = self._direct_selected_history_parts(
                        result.snapshot)
                except Exception as exc:
                    self._fail_window(refresh_target, f"HISTORICAL_PROJECTION_ERROR: {exc}")
                    return
                if not incoming:
                    self._fail_window(refresh_target, "HISTORICAL: NO_VALID_CANDLES")
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
                if (merged == list(persistent.candles)
                        and result.snapshot.request_id not in self._native_minute_receipts
                        and not (timeframe_key == "D1" and direct_query is not None)):
                    if direct_query is not None:
                        if not self._direct_selected_query_valid(
                                direct_query, stock, timeframe_key, timeframe_minutes,
                                result.snapshot.market_data_identity, cached_pool["candles"]):
                            self._fail_window(refresh_target,
                                              "HISTORICAL: SOR_DIRECT_QUERY_PROVENANCE_INVALID")
                            return
                        cached_pool["direct_selected_query"] = direct_query
                        if forming is None:
                            cached_pool.pop("selected_direct_forming_snapshot", None)
                        else:
                            cached_pool["selected_direct_forming_snapshot"] = forming
                    install_cached_pool()
                    return
                period_overlap = False
                if (
                    len(merged) < fetch_count
                    and timeframe_key in {"M60", "M120", "M240", "D1", "W1", "MO1", "Y1"}
                ):
                    cached_times = {
                        str(candle.get("time") or "")
                        for candle in persistent.candles
                    }
                    period_overlap = any(
                        str(candle.get("time") or "") in cached_times
                        for candle in incoming
                    )
                verified_nx_refresh = (result.snapshot.market_source == "NXT"
                    and result.snapshot.request_id in self._native_minute_receipts)
                if len(merged) < fetch_count and not period_overlap and not verified_nx_refresh:
                    install_cached_pool()
                    return

                request_id = str(result.snapshot.request_id or "").strip()
                pool = self._pool_from_candles(
                    fetch_session,
                    merged,
                    requested_count=persistent_capacity,
                    request_id=(request_id if request_id in self._native_minute_receipts else
                                f"{request_id or 'VALIDATION_CACHE_REFRESH'}:CACHE_MERGE"),
                    market_data_identity=result.snapshot.market_data_identity,
                    market_source=result.snapshot.market_source,
                    direct_selected_query=direct_query,
                    selected_direct_forming_snapshot=forming,
                )
                if production_calculation_enabled:
                    pool["production_session_contract"] = deepcopy(
                        production_contract
                    )
                if current_target() is None:
                    return
                if not history_extension:
                    persist_started = time.perf_counter() if _PERF_ENABLED else 0.0
                    persist_ok = self._persist_history(
                        stock,
                        timeframe_minutes,
                        timeframe_key,
                        persistent_capacity,
                        pool["candles"],
                        market_data_identity=result.snapshot.market_data_identity,
                        market_source=result.snapshot.market_source,
                        native_source_receipt=pool.get("native_minute_receipt"),
                        direct_selected_query=pool.get("direct_selected_query"),
                    )
                    if _PERF_ENABLED:
                        now = time.perf_counter()
                        _perf_emit(
                            "history.refresh.merge_persist_done",
                            attempt_started,
                            attempt_owner,
                            window_key=window_key,
                            generation=generation,
                            probe_count=count,
                            cached_rows=len(merged),
                            persist_ok=persist_ok,
                            callback_processing_ms=round(
                                (now - callback_started) * 1000.0,
                                3,
                            ),
                            persist_ms=round((now - persist_started) * 1000.0, 3),
                        )
                if current_target() is None:
                    return
                self._install_acquired_pool(
                    refresh_target,
                    session,
                    pool,
                    evaluation_count=run_request.candle_count,
                    history_browse=history_extension,
                    replacement_reason="cache_refresh_merge",
                )

            try:
                if _PERF_ENABLED:
                    attempt_started = time.perf_counter()
                    _perf_emit(
                        "history.refresh.request_start",
                        attempt_started,
                        attempt_owner,
                        window_key=window_key,
                        generation=generation,
                        probe_count=count,
                    )
                request_latest(count, refresh_completed)
            except Exception as exc:
                self._active_providers.pop(request_key, None)
                self._fail_window(target, f"HISTORICAL_REQUEST_ERROR: {exc}")

        if (not history_extension
                and timeframe_key in _LIVE_MINUTE_TIMEFRAMES - {"M1", "M3"}
                and persistent.market_source == "INTEGRATED"
                and persistent.market_data_identity == f"{stock.code}_AL"
                and isinstance(cached_pool, dict)
                and self._direct_selected_query_valid(
                    cached_pool.get("direct_selected_query"), stock, timeframe_key,
                    timeframe_minutes, persistent.market_data_identity,
                    cached_pool.get("candles"))):
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

    def _broker_al_m1_provider_options(self, session: ValidationSession) -> dict[str, bool]:
        if (
            session.request.timeframe_key == "M1"
            and self._validation_source_by_code.get(session.request.stock.code) is True
            and session.request.stock.code not in self.__dict__.get("_validation_nxt_pre_codes", ())
        ):
            return {"retain_broker_al_m1_evidence": True}
        return {}

    @staticmethod
    def _direct_m1_cache_receipt(result: object, candles: list[dict]) -> dict[str, object] | None:
        """Bind the accepted completed rows to the broker request and callback session."""
        try:
            snapshot = result.snapshot
            started, received = result.broker_al_request_receipt
            request_at = datetime.fromisoformat(started["request_as_of"])
            callback_at = datetime.fromisoformat(received["received_at"])
            epoch = started["connection_epoch"]
            login = started["login_session_id"]
            if (snapshot.timeframe_key != "M1"
                    or snapshot.market_data_identity != f"{snapshot.stock.code}_AL"
                    or snapshot.market_source != "INTEGRATED"
                    or received.get("rows") != snapshot.to_rows()
                    or started.get("rqname") != snapshot.request_id
                    or received.get("request_id") != snapshot.request_id
                    or received.get("trcode") != "opt10080"
                    or received.get("code") != snapshot.stock.code
                    or type(epoch) is not int or epoch <= 0
                    or not isinstance(login, str) or not login
                    or received.get("connection_epoch") != epoch
                    or received.get("login_session_id") != login
                    or request_at.utcoffset() != timedelta(hours=9)
                    or callback_at.utcoffset() != timedelta(hours=9)
                    or request_at > callback_at
                    or not candles):
                return None
            times = [datetime.strptime(row["time"], "%Y%m%d%H%M%S").replace(
                tzinfo=SEOUL_TIMEZONE) for row in candles]
            if (times != sorted(set(times))
                    or any(time + timedelta(minutes=1) > request_at for time in times)):
                return None
            return {
                "stock_code": snapshot.stock.code, "timeframe_key": "M1",
                "source_content_sha256": hashlib.sha256(json.dumps(
                    candles, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"), allow_nan=False,
                ).encode("utf-8")).hexdigest(),
                "connection_epoch": epoch,
                "session_sha256": hashlib.sha256(login.encode("utf-8")).hexdigest(),
                "source_as_of": request_at.isoformat(),
            }
        except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
            return None

    def _retain_broker_al_m1_result(self, window_key: int, result: object) -> None:
        if not self._retain_broker_al_m1_evidence or not isinstance(result, ValidationHistoricalResult):
            return
        snapshot = result.snapshot
        pair = result.broker_al_request_receipt
        if (
            result.ok is not True
            or not isinstance(snapshot, ValidationHistoricalSnapshot)
            or snapshot.timeframe_key != "M1"
            or snapshot.market_source != "INTEGRATED"
            or snapshot.market_data_identity != f"{snapshot.stock.code}_AL"
            or not isinstance(pair, tuple) or len(pair) != 2
            or not all(isinstance(envelope, dict) for envelope in pair)
            or pair[0].get("rqname") != snapshot.request_id
            or pair[1].get("request_id") != snapshot.request_id
        ):
            return
        self._broker_al_m1_request_receipts.setdefault(window_key, []).append(deepcopy(pair))

    def broker_al_m1_request_receipts(self, window: object) -> tuple[tuple[dict, dict], ...]:
        """Return detached source envelopes from the current V5 window run."""
        return tuple(deepcopy(self._broker_al_m1_request_receipts.get(id(window), ())))

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
            candles, forming, direct_query = self._direct_selected_history_parts(result.snapshot)
        except Exception as exc:
            self._fail_window(window, f"HISTORICAL_PROJECTION_ERROR: {exc}")
            return False
        if not candles:
            self._fail_window(window, "HISTORICAL: NO_VALID_CANDLES")
            return False

        requested_count = int(cache_requested_count or result.snapshot.requested_count)
        selected_candles = candles
        candles = merge_validation_candles([], candles, requested_count)
        if result.snapshot.timeframe_key == "M1" and direct_query is not None:
            calculation, _ = split_native_closing_price_candles(
                selected_candles, timeframe_minutes=1,
                market_data_identity=result.snapshot.market_data_identity,
                market_source=result.snapshot.market_source,
            )
            if (not candles or candles != calculation[-requested_count:]
                    or direct_query.get("source_content_sha256") != hashlib.sha256(json.dumps(
                        selected_candles, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":"), allow_nan=False,
                    ).encode("utf-8")).hexdigest()):
                self._fail_window(window, "HISTORICAL_PROJECTION_ERROR: SOR_DIRECT_HISTORY_PROVENANCE_INVALID")
                return False
            direct_query = dict(direct_query, source_content_sha256=hashlib.sha256(json.dumps(
                candles, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")).hexdigest())
        direct_m1_receipt = self._direct_m1_cache_receipt(result, candles)
        pool = self._pool_from_candles(
            persistence_session,
            candles,
            requested_count=requested_count,
            request_id=result.snapshot.request_id,
            market_data_identity=result.snapshot.market_data_identity,
            market_source=result.snapshot.market_source,
            direct_selected_query=direct_query,
            selected_direct_forming_snapshot=forming,
        )
        if production_contract is not None:
            pool["production_session_contract"] = deepcopy(production_contract)
        if not history_browse:
            self._persist_history(
                persistence_session.request.stock,
                persistence_session.request.timeframe_minutes,
                result.snapshot.timeframe_key,
                requested_count,
                pool["candles"],
                market_data_identity=result.snapshot.market_data_identity,
                market_source=result.snapshot.market_source,
                direct_m1_receipt=direct_m1_receipt,
                native_source_receipt=pool.get("native_minute_receipt"),
                direct_selected_query=pool.get("direct_selected_query"),
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
        if self._verified_sor_tail_provider is not None and pool.get("market_source") not in {"NXT", "KRX"}:
            if not pool.get("sor_tail_calculation_complete"):
                self._fail_window(window, "SOR_VERIFIED_BLOCKED:REPLAY_VIEW_UNVERIFIED")
                return
        elif chart_range is not None:
            try:
                pool = self._range_replay_history_view(session, pool, history_target)
            except ValueError as exc:
                self._fail_window(window, f"REPLAY_ERROR: {exc}")
                return
        else:
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

    def _start_marking(self, window, request) -> None:
        if window is None or self._open_windows.get(id(window)) is not window:
            return
        key = id(window)
        request_generation = self._request_generation.get(key, 0)
        _trace_v5_marking("marking_requested", key, request.get("generation"),
                          request_generation,
                          calculation_count=(len(request["calculation"]) if isinstance(
                              request.get("calculation"), (list, tuple)) else None),
                          display_count=(len(request["display"]) if isinstance(
                              request.get("display"), (list, tuple)) else None))
        source_session = self._validation_sessions.get(key)
        pool = self._historical_pools.get(key)
        try:
            if source_session is None or not isinstance(pool, dict):
                raise ValueError("MARKING_SOURCE_UNAVAILABLE")
            settings = request["settings"]
            minutes = settings.to_dict()["bar"]["bar_minutes"]
            if minutes != source_session.request.timeframe_minutes:
                raise ValueError("MARKING_TIMEFRAME_MISMATCH")
            self._require_selected_timeframe_pool(source_session, pool)
            session = ValidationSession(ValidationRequest(window.stock, settings, minutes),
                                        operation_active_reader=self._operation_active_reader)
            readiness = session.readiness()
            if not readiness.allowed:
                raise ValueError(str(readiness.reason))
            published_m1 = (
                self._published_m1_marking_basis(
                    window, source_session, pool, request_generation, request)
                if session.request.stock.code == "005930"
                and session.request.timeframe_key == "M1" else None
            )
            source_rows = (published_m1[0].get("candles", ()) if published_m1
                           else pool.get("candles", ()))
            if pool.get("native_live_snapshot") is not None:
                source_view, deficit = self._normal_base_history_view(
                    source_session, pool,
                    self._history_targets.get(key, self._historical_count))
                if deficit:
                    raise ValueError("MARKING_SOURCE_UNAVAILABLE")
                source_rows = source_view["candles"]
            elif source_session.request.timeframe_key in {"D1", "W1", "MO1", "Y1"}:
                source_view = self._validation_pool_for_session(
                    source_session, pool,
                    target_count=max(len(source_rows), len(request["calculation"])))
                source_rows = source_view["candles"]
            source_by_time = {
                str(row.get("time") or ""): row
                for row in source_rows
            }
            detached_premarket = [
                row for row in request["calculation"]
                if (
                    "0800" <= str(row.get("time") or "")[8:12] < "0850"
                    and str(row.get("time") or "") not in source_by_time
                )
            ]
            verified = {}
            if detached_premarket:
                sidecar = self._integrated_display_sidecars.get(
                    self._integrated_display_sidecar_key(session)
                )
                verified = {
                    row["time"]: row
                    for row in self._verified_validation_premarket_candles(
                        session, sidecar
                    )
                }
                if any(verified.get(row["time"]) != row
                       for row in detached_premarket):
                    raise ValueError("MARKING_PREMARKET_SOURCE_NOT_VERIFIED")
            generation = request["generation"]
            request_generation = self._request_generation.get(key, 0)
            publication = pool.get("_dispatch_snapshot")
            source_identity = (
                publication[3] if isinstance(publication, tuple)
                and len(publication) == 4 else ()
            )
            direct_snapshot = (published_m1[1] if published_m1 else
                self._sor_m1_calculation_snapshot(session, pool)
                if (session.request.stock.code == "005930"
                    and session.request.timeframe_key in {"M1", "M3"}
                    and pool.get("native_live_snapshot") is None)
                else None)
        except (TypeError, ValueError, KeyError) as exc:
            _trace_v5_marking("marking_failed", key, request.get("generation"),
                              request_generation, reason=str(exc), phase="precheck",
                              exception_type=type(exc).__name__)
            window._disable_marking(clear_cache=True)
            window.marking_display_check.setToolTip(str(exc))
            return

        def current():
            return (self._open_windows.get(key) is window
                    and self._request_generation.get(key, 0) == request_generation
                    and window._marking_generation == generation
                    and window._marking_key == request["key"]
                    and (published_m1 is None or getattr(
                        window, "_v5_m1_marking_basis", None) is published_m1[2]))

        @_perf_stage("worker.marking", owner=window)
        def prepare():
            payload = dict(window_key=key, generation=generation, key=request["key"],
                           request_generation=request_generation, snapshot=None, error="")
            if published_m1 is not None:
                payload["m1_marking_basis"] = published_m1[2]
                payload["m1_source_session"] = source_session
                payload["m1_source_pool"] = pool
                payload["m1_request"] = request
            try:
                if not current():
                    _trace_v5_marking("marking_stale", key, generation,
                                      request_generation)
                    return
                source_candles = [
                    source_by_time[row["time"]]
                    for row in request["calculation"]
                    if row["time"] in source_by_time
                ]
                if not source_candles or len(source_by_time) != len(source_rows):
                    raise ValueError("MARKING_SOURCE_UNAVAILABLE")
                if source_candles != source_rows[-len(source_candles):]:
                    raise ValueError("SOR_VERIFIED_BLOCKED:MARKING_SOURCE_CHANGED")
                replay_view = dict(
                    published_m1[0] if published_m1 else pool,
                    candles=source_candles,
                    display_candles=list(source_candles),
                    display_only_integrated=False,
                )
                if self._verified_sor_tail_provider is not None:
                    now = self._now_factory()
                    now = (now.replace(tzinfo=SEOUL_TIMEZONE) if now.tzinfo is None
                           else now.astimezone(SEOUL_TIMEZONE))
                    replay_view = self._verified_sor_view_for_session(
                        session, pool, replay_view, now, source_identity,
                    )
                calculation = request["calculation"]
                display = request["display"]
                def check_marking_view(checked_view):
                    if (published_m1 is not None
                            and (pool.get("_dispatch_snapshot") is not publication
                                 or source_identity != self._pool_computation_source_identity(pool))):
                        raise ValueError("SOR_VERIFIED_BLOCKED:MARKING_SOURCE_CHANGED")
                    proven = {row["time"]: row for row in checked_view["candles"]}
                    calculation_times = [row["time"] for row in calculation]
                    display_times = [row["time"] for row in display]
                    calculation_by_time = {row["time"]: row for row in calculation}
                    if (len(proven) != len(checked_view["candles"])
                            or calculation_times != sorted(set(calculation_times))
                            or display_times != sorted(set(display_times))
                            or any((verified.get(row["time"]) if row["time"] in verified
                                    else proven.get(row["time"])) != row
                                   for row in calculation)
                            or any(calculation_by_time.get(row["time"]) != row
                                   for row in display)):
                        raise ValueError("SOR_VERIFIED_BLOCKED:MARKING_SOURCE_CHANGED")
                    checked_view = dict(
                        checked_view, candles=calculation, display_candles=display,
                        display_only_integrated=False,
                    )
                    if detached_premarket:
                        checked_view["market_source"] = "VALIDATION_COMPOSITE"
                        checked_view["market_data_identity"] = (
                            f"{session.request.stock.code}_AL_NXT_PRE_VALIDATION"
                        )
                        checked_view["snapshot"] = ValidationHistoricalSnapshot(
                            stock=session.request.stock,
                            timeframe_minutes=session.request.timeframe_minutes,
                            timeframe_key=session.request.timeframe_key,
                            requested_count=len(calculation),
                            request_id=str(checked_view.get("request_id") or "POOL"),
                            rows=self._historical_rows_from_candles(calculation),
                            market_data_identity=checked_view["market_data_identity"],
                            market_source=checked_view["market_source"],
                        )
                    return checked_view

                deferred_check = (session.request.timeframe_key == "M1"
                                  and direct_snapshot is not None)
                if not deferred_check:
                    replay_view = check_marking_view(replay_view)
                result = self._prepare_replay_result_from_pool(
                    session, pool if direct_snapshot is not None else replay_view,
                    history_target=len(request["display"]),
                    chart_range=request["range"], chart_range_times=request["times"],
                    prepared_view_pool=replay_view, cancel_predicate=lambda: not current(),
                    sor_m1_calculation_snapshot=direct_snapshot,
                    expected_sor_m1_calculation=(
                        calculation if direct_snapshot is not None else None),
                    marking_view_check=(check_marking_view if deferred_check else None))
                snapshot = result.snapshot
                # Build only marking comparisons; normal filters retain their original cache.
                rows = snapshot.to_candles()
                replay_entries = snapshot.to_entries()
                cache, _ = _build_validation_projection_data(
                    rows, replay_entries, [], tuple(range(len(rows))),
                    settings.to_dict(), request["descriptors"],
                    cancel_predicate=lambda: not current())
                payload["snapshot"] = snapshot
                payload["comparison"] = (tuple(c["time"] for c in rows), cache)
                if (minutes in SUPPORTED_BAR_MINUTES
                        and session.request.timeframe_key == f"M{minutes}"
                        and not detached_premarket):
                    try:
                        marker_path = self._validation_cache_source_marker_path(
                            session.request.stock, minutes, session.request.timeframe_key,
                            int(pool.get("requested_count") or 0))
                        if (marker_path is not None and marker_path.is_file()
                                and marker_path.stat().st_size <= 4096):
                            source_marker = json.loads(marker_path.read_text(encoding="utf-8"))
                            evidence = build_v5_records(
                                pool, source_marker, snapshot, rows, replay_entries,
                                generation=generation,
                                request_generation=request_generation,
                                window_key=key)
                            if evidence:
                                payload["sor_m3_evidence_lines"] = "".join(
                                    json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n"
                                    for record in evidence)
                                payload["sor_evidence_timeframe"] = session.request.timeframe_key
                    except (OSError, ValueError, TypeError):
                        pass  # Optional evidence cannot interrupt marking.
            except Exception as exc:
                payload["error"] = str(exc)
                failure_type = type(exc).__name__
            if current():
                if payload["error"]:
                    _trace_v5_marking("marking_failed", key, generation,
                                      request_generation, reason=payload["error"],
                                      phase="worker", exception_type=failure_type)
                else:
                    _trace_v5_marking("marking_calculated", key, generation,
                                      request_generation,
                                      calculation_count=snapshot.candle_count,
                                      display_count=len(request["display"]),
                                      entry_count=len(snapshot._entries))
                try:
                    self.marking_completed.emit(payload)
                except RuntimeError:
                    _trace_v5_marking("marking_stale", key, generation,
                                      request_generation)
            else:
                _trace_v5_marking("marking_stale", key, generation,
                                  request_generation)

        try:
            Thread(target=prepare, name="indicator-follow-v5-marking", daemon=True).start()
        except Exception as exc:
            _trace_v5_marking("marking_failed", key, generation,
                              request_generation, reason=str(exc), phase="thread_start",
                              exception_type=type(exc).__name__)
            window._disable_marking(clear_cache=True)
            window.marking_display_check.setToolTip(str(exc))

    def _on_marking_completed(self, payload) -> None:
        if isinstance(payload, dict) and payload.get("source_revision") is True:
            self._on_marking_source_revision_completed(payload)
            return
        window = self._open_windows.get(payload["window_key"])
        if (window is None or payload["generation"] != window._marking_generation
                or payload["key"] != window._marking_key
                or payload["request_generation"] != self._request_generation.get(id(window), 0)
                or ("m1_marking_basis" in payload
                    and payload["m1_marking_basis"] is not getattr(
                        window, "_v5_m1_marking_basis", None))):
            _trace_v5_marking("marking_stale", payload["window_key"],
                              payload["generation"], payload["request_generation"])
            return
        try:
            if "m1_marking_basis" in payload:
                self._published_m1_marking_basis(
                    window, payload["m1_source_session"], payload["m1_source_pool"],
                    payload["request_generation"], payload["m1_request"])
            if payload.get("error"):
                raise ValueError(payload["error"])
            published = window.apply_marking_snapshot(
                payload["generation"], payload["key"], payload["snapshot"],
                payload["comparison"])
            if published and payload.get("sor_m3_evidence_lines"):
                token = (payload["generation"], payload["request_generation"], payload["key"])
                key = payload["window_key"]
                if self._sor_m3_marking_published.get(key) != token:
                    timeframe = payload.get("sor_evidence_timeframe", "M3")
                    queued = (_queue_v5_m3_evidence(payload["sor_m3_evidence_lines"])
                              if timeframe == "M3" else _queue_v5_m3_evidence(
                                  payload["sor_m3_evidence_lines"], timeframe=timeframe))
                    if queued:
                        self._sor_m3_marking_published[key] = token
            _trace_v5_marking("marking_published" if published else "marking_stale",
                              payload["window_key"], payload["generation"],
                              payload["request_generation"])
        except (TypeError, ValueError, KeyError) as exc:
            if not payload.get("error"):
                _trace_v5_marking("marking_failed", payload["window_key"],
                                  payload["generation"], payload["request_generation"],
                                  reason=str(exc), phase="publication",
                                  exception_type=type(exc).__name__)
            window._disable_marking(clear_cache=True)
            window.marking_display_check.setToolTip(str(exc))

    def _on_marking_source_revision_completed(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        window_key = payload.get("window_key")
        token = payload.get("revision_token")
        state = (
            self._marking_source_revisions.get(window_key)
            if isinstance(window_key, int) and not isinstance(window_key, bool)
            else None
        )
        if not isinstance(state, dict) or state.get("token") is not token:
            return
        window = self._open_windows.get(window_key)
        session = payload.get("session")
        pool = payload.get("pool")

        def fail_installed_candidate(error: object) -> None:
            if window is None or state.get("candidate_installed") is not True:
                return
            fail = getattr(window, "fail_marking_source_revision", None)
            if callable(fail):
                fail(
                    generation=payload.get("generation"),
                    previous_key=payload.get("key"),
                    times=payload.get("times"),
                    settings=payload.get("settings"),
                    error=error,
                )

        if (
            window is None
            or self._request_generation.get(window_key, 0)
            != payload.get("request_generation")
            or self._history_targets.get(
                window_key,
                self._historical_count,
            ) != payload.get("history_target")
            or self._validation_sessions.get(window_key) is not session
            or self._historical_pools.get(window_key) is not pool
            or state.get("identity") != payload.get("revision_identity")
            or (
                state.get("source_kind") == "integrated"
                and self._integrated_display_sidecars.get(
                    self._integrated_display_sidecar_key(session)
                ) is not state.get("sidecar")
            )
        ):
            state["status"] = "rejected"
            fail_installed_candidate("MARKING_SOURCE_REVISION_STALE_FLOW_IDENTITY")
            trace_signal_validation_event(
                "flow.marking_source_revision.rejected",
                window_key=window_key,
                reason="STALE_FLOW_IDENTITY",
            )
            return
        error = str(payload.get("error") or "").strip()
        try:
            calculation = payload.get("calculation")
            display = payload.get("display")
            if error:
                raise ValueError(error)
            current_sidecar = state.get("sidecar")
            if (
                self._marking_revision_candle_identity(calculation)
                != payload.get("calculation_content")
                or self._marking_revision_candle_identity(display)
                != payload.get("display_identity")
            ):
                raise ValueError("MARKING_SOURCE_REVISION_CONTENT_MISMATCH")
            if state.get("source_kind") == "integrated":
                if (
                    not isinstance(current_sidecar, dict)
                    or self._marking_revision_candle_identity(
                        current_sidecar.get("candles")
                    ) != state.get("sidecar_content")
                    or (
                        current_sidecar.get("market_data_identity"),
                        current_sidecar.get("market_source"),
                        current_sidecar.get("request_as_of"),
                        current_sidecar.get("coverage_count"),
                        state.get("sidecar_content"),
                        current_sidecar.get("verified_nxt_eligible"),
                    ) != state["identity"][6]
                ):
                    raise ValueError("MARKING_SOURCE_REVISION_CONTENT_MISMATCH")
            elif (
                payload.get("calculation_content")
                != state.get("calculation_content")
                or payload.get("display_identity")
                != state.get("display_content")
            ):
                raise ValueError("MARKING_SOURCE_REVISION_CONTENT_MISMATCH")
            publish = getattr(window, "publish_marking_source_revision", None)
            if not callable(publish):
                raise ValueError("MARKING_SOURCE_REVISION_API_UNAVAILABLE")
            accepted = publish(
                generation=payload["generation"],
                previous_key=payload["key"],
                candidate_key=payload["candidate_key"],
                times=payload["times"],
                source_start=payload["source_start"],
                settings=payload["settings"],
                calculation_candles=calculation,
                display_candles=display,
                display_only_integrated=payload["display_only_integrated"],
                snapshot=payload["snapshot"],
                comparison=payload["comparison"],
                normal_base_presentation=payload["normal_base_presentation"],
                chart_candle_count=payload["history_target"],
                timeframe_key=session.request.timeframe_key,
            )
            if not accepted:
                state["status"] = "rejected"
                fail_installed_candidate(
                    "MARKING_SOURCE_REVISION_STALE_MARKING_IDENTITY"
                )
                trace_signal_validation_event(
                    "flow.marking_source_revision.rejected",
                    window_key=window_key,
                    reason="STALE_MARKING_IDENTITY",
                )
                return
        except (TypeError, ValueError, KeyError) as exc:
            state["status"] = "error"
            state["error"] = str(exc)
            if state.get("candidate_installed") is True:
                fail_installed_candidate(exc)
            else:
                window.marking_display_check.setToolTip(str(exc))
            trace_signal_validation_event(
                "flow.marking_source_revision.failed",
                window_key=window_key,
                reason=str(exc),
            )
            return
        state["status"] = "published"
        state["recalculated"] = payload.get("recalculated") is True
        trace_signal_validation_event(
            "flow.marking_source_revision.published",
            window_key=window_key,
            request_generation=payload.get("request_generation"),
            marking_generation=payload.get("generation"),
            recalculated=payload.get("recalculated") is True,
            calculation_identity=payload.get("calculation_identity"),
            display_identity=payload.get("display_identity"),
        )

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
        source_session = self._validation_sessions.get(window_key)
        if pool is None or source_session is None:
            return
        try:
            session = self._range_replay_session(window, source_session)
        except (TypeError, ValueError, KeyError) as exc:
            self._fail_window(window, f"RANGE_REPLAY_SETTINGS_ERROR: {exc}")
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
            "source_session": source_session,
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

    def _range_replay_session(
        self,
        window: object,
        source_session: ValidationSession,
    ) -> ValidationSession:
        capture = getattr(window, "_marking_current_settings", None)
        if not callable(capture):
            raise ValueError("RANGE_REPLAY_CURRENT_SETTINGS_UNAVAILABLE")
        settings = capture()
        if not isinstance(settings, ValidationSettingsSnapshot):
            raise ValueError("RANGE_REPLAY_CURRENT_SETTINGS_INVALID")
        rules = settings.to_dict()
        source_rules = source_session.request.settings_snapshot.to_dict()
        # The selected range keeps its displayed source timeframe and only
        # refreshes the draft's buy/sell policy.
        rules["bar"] = deepcopy(source_rules["bar"])
        if "validation_timeframe" in source_rules:
            rules["validation_timeframe"] = deepcopy(
                source_rules["validation_timeframe"]
            )
        settings = ValidationSettingsSnapshot(rules)
        return ValidationSession(
            ValidationRequest(
                source_session.request.stock,
                settings,
                source_session.request.timeframe_minutes,
            ),
            operation_active_reader=self._operation_active_reader,
        )

    def _range_replay_settings_are_current(
        self,
        window: object,
        session: object,
    ) -> bool:
        if not isinstance(session, ValidationSession):
            return False
        try:
            current = self._range_replay_session(window, session)
        except (TypeError, ValueError, KeyError):
            return False
        return (
            current.request.settings_snapshot.rules_hash
            == session.request.settings_snapshot.rules_hash
        )

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
            and self._validation_sessions.get(window_key)
            is request.get("source_session")
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
            and self._validation_sessions.get(window_key)
            is request.get("source_session")
        )

    def _start_range_replay(self, request: dict[str, object]) -> None:
        window_key = request.get("window_key")
        if (
            not isinstance(window_key, int)
            or self._open_windows.get(window_key) is not request.get("window")
            or self._range_replay_generation.get(window_key) != request.get("generation")
            or self._request_generation.get(window_key, 0) != request.get("request_generation")
            or self._historical_pools.get(window_key) is not request.get("pool")
            or self._validation_sessions.get(window_key)
            is not request.get("source_session")
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
        publication = pool.get("_dispatch_snapshot")
        verified_source_identity = (
            (publication[3] if isinstance(publication, tuple)
             and len(publication) == 4 else ())
            if self._verified_sor_tail_provider is not None else None
        )
        window_calculation = getattr(window, "_calculation_candles", None)
        direct_receipt = pool.get("selected_direct_forming_snapshot")
        receipt_candle = (direct_receipt.get("candle")
                          if isinstance(direct_receipt, dict) else None)
        replaying_direct_current = (
            session.request.stock.code == "005930"
            and session.request.timeframe_key == "M3"
            and pool.get("native_live_snapshot") is None
            and isinstance(window_calculation, list) and bool(window_calculation)
            and isinstance(window_calculation[-1], dict)
            and isinstance(receipt_candle, dict)
            and window_calculation[-1].get("time") == receipt_candle.get("time")
        )
        published_direct_calculation = None
        if replaying_direct_current:
            try:
                published_direct_calculation = self._published_direct_current_snapshot(
                    window, request.get("source_session"), pool, request_generation)
            except Exception:
                pass  # Invalid binding remains blocked by the replay worker.
        sor_m1_calculation_snapshot = (
            published_direct_calculation if published_direct_calculation is not None
            else self._sor_m1_calculation_snapshot(session, pool)
        )
        displayed_calculation = (
            deepcopy(window_calculation)
            if sor_m1_calculation_snapshot is not None else None
        )
        if (sor_m1_calculation_snapshot is not None
                and not isinstance(displayed_calculation, list)):
            displayed_calculation = []
        reusable_cost_replays = (
            dict(pool.get("trading_cost_replay_cache"))
            if isinstance(pool.get("trading_cost_replay_cache"), dict)
            else {}
        )

        @_perf_stage("worker.prepare_range_replay", owner=window)
        def prepare_range_replay() -> None:
            payload: dict[str, object] = {
                "window_key": window_key,
                "generation": generation,
                "request_generation": request_generation,
                "range_token": token,
                "pool": pool,
                "session": session,
                "source_session": request.get("source_session"),
                "range": requested_range,
                "range_times": request.get("range_times"),
                "published_direct_calculation": published_direct_calculation,
                "calculation_range_times": request.get(
                    "calculation_range_times"
                ),
                "snapshot": None,
                "error": "",
            }
            try:
                if replaying_direct_current and published_direct_calculation is None:
                    raise ValueError("SOR_M1_BLOCKED:DISPLAYED_CALCULATION_CHANGED")
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
                    reusable_cost_replays=reusable_cost_replays,
                    verified_source_identity=verified_source_identity,
                    sor_m1_calculation_snapshot=sor_m1_calculation_snapshot,
                    expected_sor_m1_calculation=displayed_calculation,
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
                payload["trading_cost_replay_key"] = (
                    self._trading_cost_replay_key(
                        session,
                        pool,
                        result.snapshot.to_candles(),
                        result.snapshot.evaluated_start_index,
                        result.snapshot.evaluated_end_index,
                    )
                )
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
                "source_session": request.get("source_session"),
                "range": requested_range,
                "range_times": request.get("range_times"),
                "published_direct_calculation": published_direct_calculation,
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
            or self._validation_sessions.get(window_key)
            is not payload.get("source_session")
        ):
            self._range_replay_active.pop(window_key, None)
            return
        published_direct_calculation = payload.get("published_direct_calculation")
        if published_direct_calculation is not None:
            try:
                still_current = self._published_direct_current_snapshot(
                    window, payload.get("source_session"), pool,
                    request_generation) is published_direct_calculation
            except Exception:
                still_current = False
            if not still_current:
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
        if (
            not isinstance(session, ValidationSession)
            or snapshot.settings_hash
            != session.request.settings_snapshot.rules_hash
            or not self._range_replay_settings_are_current(window, session)
        ):
            self._range_replay_active.pop(window_key, None)
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
        self._publish_trading_cost_replay(
            pool,
            payload.get("trading_cost_replay_key"),
            snapshot,
        )
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
            apply_range(
                snapshot,
                settings_snapshot=session.request.settings_snapshot,
            )
        except Exception as exc:
            self._fail_window(window, f"RESULT_VIEW_ERROR: {exc}")
        self._start_pending_window_work(window_key)


    def _fail_window(self, window: object, message: str) -> None:
        text = str(message or "VALIDATION_BLOCKED")
        window_key = id(window)
        self._history_extension_pending.pop(window_key, None)
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


def bind_indicator_follow_signal_validation_v5_flow(
    owner: object,
    dialog: object,
) -> IndicatorFollowSignalValidationV5Flow | None:
    signal = getattr(dialog, "signal_validation_v5_requested", None)
    if not callable(getattr(signal, "connect", None)):
        return None
    flow = getattr(owner, _OWNER_FLOW_ATTRIBUTE, None)
    if not isinstance(flow, IndicatorFollowSignalValidationV5Flow):
        qobject_parent = owner if isinstance(owner, QObject) else None
        flow = IndicatorFollowSignalValidationV5Flow(
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
