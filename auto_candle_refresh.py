# -*- coding: utf-8 -*-
"""Minute-cycle coordinator for bounded opt10080 candle refreshes."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PyQt5.QtCore import QTimer

from candle_manager import DEFAULT_CANDLES_MAX_COUNT, load_candles
from candle_timeframe_aggregation import (
    MARKET_BUCKET_ANCHOR,
    SEOUL_TIMEZONE,
    candle_market_datetime,
    filter_candles_by_trade_date,
    parse_market_datetime,
)
from gui_auto_trade_runtime import all_registered_stock_dirs, parse_stock_folder_name
from gui_stock_data import PROJECT_ROOT
from stock_market_source_plan import read_stock_market_source_plan, source_for_time
from runtime_io import read_json_dict
from kiwoom_market_data_authority import (
    NORMAL_TR_REFRESH,
    REALTIME_PRIMARY_SKIP,
    REALTIME_RECONCILIATION,
    REALTIME_RECONCILIATION_REQUEST,
)


BOOTSTRAP_REQUEST_COUNT = 600
INCREMENTAL_REQUEST_COUNT = 3
MAX_REQUESTS_PER_CYCLE = 15
REQUEST_SPACING_MS = 1_000
BOOTSTRAP_BATCH_PAUSE_MS = 60_000


RefreshCompletion = Callable[[dict[str, Any]], None]


def request_operation_candle_for_stock(
    window: Any,
    stock_dir: Path,
    code: str,
    name: str,
    *,
    operation_cycle_minute_key: str,
    request_kind: str = NORMAL_TR_REFRESH,
    reconciliation_minute: str = "",
    count: int = INCREMENTAL_REQUEST_COUNT,
    on_terminal: RefreshCompletion | None = None,
) -> dict[str, Any]:
    """Use the existing governed opt10080 entrypoint for normal or fallback work."""
    api = _api_from_host(window)
    request = getattr(api, "request_minute_candles", None)
    if not callable(request):
        result = {"ok": False, "error": "request_minute_candles unavailable"}
        if callable(on_terminal):
            on_terminal(dict(result))
        return result
    callback_called = False
    owned_rqname = ""
    retention_max_count = DEFAULT_CANDLES_MAX_COUNT
    required_count_getter = getattr(window, "candle_history_required_count", None)
    if callable(required_count_getter):
        try:
            required_count = int(required_count_getter(code))
        except (TypeError, ValueError, OverflowError):
            required_count = DEFAULT_CANDLES_MAX_COUNT
        retention_max_count = min(
            DEFAULT_CANDLES_MAX_COUNT,
            max(1, int(count), required_count),
        )

    def received(result: dict[str, Any]) -> None:
        nonlocal callback_called
        if callback_called:
            return
        callback_called = True
        terminal = dict(result) if isinstance(result, dict) else {"ok": False, "error": "malformed result"}
        terminal_rqname = str(terminal.get("rqname") or "").strip() or owned_rqname
        complete = getattr(window, "complete_operation_candle_request", None)
        if terminal_rqname and callable(complete):
            complete(terminal_rqname)
        if callable(on_terminal):
            on_terminal(terminal)

    try:
        started = request(
            code,
            name,
            interval=1,
            count=count,
            max_count=retention_max_count,
            callback=received,
        )
    except Exception as exc:
        started = {"ok": False, "error": str(exc)}
    if isinstance(started, dict) and started.get("ok") is True and not callback_called:
        owned_rqname = str(started.get("rqname") or "").strip()
        register = getattr(window, "register_operation_candle_request", None)
        if owned_rqname and callable(register):
            kwargs = {
                "stock_code": code,
                "stock_name": name,
                "stock_dir": stock_dir,
                "operation_cycle_minute_key": operation_cycle_minute_key,
            }
            if request_kind != NORMAL_TR_REFRESH or reconciliation_minute:
                kwargs.update(
                    request_kind=request_kind,
                    reconciliation_minute=reconciliation_minute,
                )
            register(owned_rqname, **kwargs)
    if isinstance(started, dict) and started.get("ok") is not True and not callback_called:
        received(started)
    return dict(started) if isinstance(started, dict) else {"ok": False, "error": "malformed start result"}


def request_direct_timeframe_candles_for_stock(
    window: Any,
    code: str,
    name: str,
    *,
    operation_cycle_minute_key: str,
    timeframe_minutes: int,
    count: int,
    on_terminal: RefreshCompletion | None = None,
) -> dict[str, Any]:
    """Fetch selected-timeframe history without mutating canonical M1 storage."""
    api = _api_from_host(window)
    request = getattr(api, "request_minute_candles_read_only", None)
    install = getattr(window, "install_direct_timeframe_candles", None)
    if not callable(request) or not callable(install):
        try:
            interval_hint = int(timeframe_minutes)
        except (TypeError, ValueError):
            interval_hint = -1
        result = {
            "ok": False,
            "reason_code": "DIRECT_TIMEFRAME_API_UNAVAILABLE",
            "timeframe_minutes": interval_hint,
        }
        if callable(on_terminal):
            on_terminal(dict(result))
        return result

    try:
        interval = max(int(timeframe_minutes), 1)
        request_count = min(
            max(int(count), 1),
            DEFAULT_CANDLES_MAX_COUNT,
        )
    except (TypeError, ValueError):
        result = {
            "ok": False,
            "reason_code": "DIRECT_TIMEFRAME_REQUEST_INVALID",
        }
        if callable(on_terminal):
            on_terminal(dict(result))
        return result

    callback_called = False
    as_of = (
        parse_market_datetime(operation_cycle_minute_key)
        or datetime.now(SEOUL_TIMEZONE)
    )

    def received(result: dict[str, Any]) -> None:
        nonlocal callback_called
        if callback_called:
            return
        callback_called = True
        terminal = (
            dict(result)
            if isinstance(result, dict)
            else {"ok": False, "error": "malformed result"}
        )
        if terminal.get("ok") is True:
            response_code = str(terminal.get("code") or "").strip()
            try:
                response_interval = int(terminal.get("interval"))
            except (TypeError, ValueError):
                response_interval = -1
            if (
                response_code != str(code).strip()
                or response_interval != interval
                or terminal.get("market_data_identity") != str(code).strip()
                or terminal.get("market_source") != "KRX"
                or bool(terminal.get("display_only"))
            ):
                terminal.update(
                    ok=False,
                    reason_code="DIRECT_TIMEFRAME_RESPONSE_IDENTITY_INVALID",
                )
            else:
                installed = install(
                    code,
                    interval,
                    terminal.get("rows"),
                    as_of=as_of,
                )
                terminal["install_result"] = (
                    dict(installed)
                    if isinstance(installed, dict)
                    else {"ok": False, "reason_code": "DIRECT_TIMEFRAME_INSTALL_INVALID"}
                )
                if terminal["install_result"].get("ok") is not True:
                    terminal["ok"] = False
                    terminal["reason_code"] = str(
                        terminal["install_result"].get("reason_code")
                        or "DIRECT_TIMEFRAME_INSTALL_FAILED"
                    )
        if callable(on_terminal):
            on_terminal(terminal)

    try:
        started = request(
            code,
            name,
            interval=interval,
            count=request_count,
            callback=received,
        )
    except Exception as exc:
        started = {
            "ok": False,
            "error": str(exc),
            "reason_code": "DIRECT_TIMEFRAME_REQUEST_FAILED",
        }
    if (
        isinstance(started, dict)
        and started.get("ok") is not True
        and not callback_called
    ):
        received(started)
    return (
        dict(started)
        if isinstance(started, dict)
        else {"ok": False, "error": "malformed start result"}
    )


def request_display_timeframe_candles_for_stock(
    window: Any,
    code: str,
    name: str,
    *,
    operation_cycle_minute_key: str,
    timeframe_minutes: int,
    count: int,
    nxt_available: bool | None,
    on_terminal: RefreshCompletion | None = None,
) -> dict[str, Any]:
    """Fetch verified integrated history into the display-only cache."""
    api = _api_from_host(window)
    request = getattr(
        api,
        "request_minute_candles_display_only_integrated",
        None,
    )
    install = getattr(window, "install_display_timeframe_candles", None)
    if not callable(request) or not callable(install) or nxt_available is not True:
        result = {
            "ok": False,
            "reason_code": "DISPLAY_TIMEFRAME_API_UNAVAILABLE",
        }
        if callable(on_terminal):
            on_terminal(dict(result))
        return result
    try:
        interval = max(int(timeframe_minutes), 1)
        request_count = min(max(int(count), 1), DEFAULT_CANDLES_MAX_COUNT)
    except (TypeError, ValueError):
        result = {
            "ok": False,
            "reason_code": "DISPLAY_TIMEFRAME_REQUEST_INVALID",
        }
        if callable(on_terminal):
            on_terminal(dict(result))
        return result

    callback_called = False
    as_of = (
        parse_market_datetime(operation_cycle_minute_key)
        or datetime.now(SEOUL_TIMEZONE)
    )
    try:
        plan = read_stock_market_source_plan(PROJECT_ROOT, code, as_of.date())
    except ValueError:
        plan = None
    if plan is None or plan["nxt_available"] is not True:
        result = {"ok": False, "reason_code": "MARKET_SOURCE_PLAN_UNAVAILABLE"}
        if callable(on_terminal):
            on_terminal(dict(result))
        return result
    active = source_for_time(plan, as_of)
    if active is not None and active["source"] != "SOR":
        result = {"ok": False, "reason_code": "MARKET_SOURCE_SESSION_MISMATCH"}
        if callable(on_terminal):
            on_terminal(dict(result))
        return result
    try:
        session = api.broker_session_snapshot()
        expected_epoch = session.connection_epoch
        expected_session = session.login_session_id
        session_ready = (
            session.connected is True
            and type(expected_epoch) is int
            and expected_epoch > 0
            and isinstance(expected_session, str)
            and bool(expected_session)
        )
    except Exception:
        session_ready = False
    if not session_ready:
        result = {"ok": False, "reason_code": "DISPLAY_TIMEFRAME_SESSION_UNAVAILABLE"}
        if callable(on_terminal):
            on_terminal(dict(result))
        return result

    # Audit only a day that an independent SOR producer already published.
    # No new TR, no broker-source promotion, no change to display admission.
    native_auditor = None
    native_day = native_plan = None
    if interval == 1:
        audit = getattr(window, "accept_broker_al_m1_receipt", None)
        day_reader = getattr(window, "published_sor_day_for_native_receipt", None)
        if callable(audit) and callable(day_reader):
            try:
                candidate = day_reader(code)
                frozen = getattr(candidate, "session", None)
                if (frozen is not None
                        and frozen.stock_code == code
                        and frozen.connection_epoch == expected_epoch
                        and frozen.login_session_id == expected_session):
                    from indicator_follow_validation_v5_broker_al_m1_request_evidence import (
                        freeze_broker_al_m1_request_plan,
                    )
                    from sor_broker_al_m1_handoff import latest_native_m1_receipt_window

                    receipt_window = latest_native_m1_receipt_window(
                        candidate, request_count,
                    )
                    if receipt_window is not None:
                        native_plan = freeze_broker_al_m1_request_plan(
                            expected_epoch, expected_session, code,
                            *receipt_window,
                        )
                        native_day, native_auditor = candidate, audit
            except Exception:
                # Optional audit cannot interrupt the normal display request.
                native_day = native_plan = native_auditor = None
    native_started = None
    native_terminals: list[dict[str, Any]] = []

    original_display_request = request

    def request(*args: Any, **kwargs: Any) -> Any:
        nonlocal native_started
        # Observe only the result of the same existing governed TR request.
        result = original_display_request(*args, **kwargs)
        native_started = result
        for pending_receipt in native_terminals:
            audit_native_receipt(pending_receipt)
        native_terminals.clear()
        return result

    def audit_native_receipt(terminal: dict[str, Any]) -> None:
        if (native_plan is None or native_auditor is None
                or not isinstance(native_started, dict)
                or native_started.get("ok") is not True):
            return
        try:
            native_auditor(
                native_plan, native_started, terminal,
                independently_verified_day=native_day,
            )
        except Exception:
            # An independent audit must not change existing display behavior.
            pass

    def received(result: dict[str, Any]) -> None:
        nonlocal callback_called
        if callback_called:
            return
        callback_called = True
        terminal = (
            dict(result)
            if isinstance(result, dict)
            else {"ok": False, "error": "malformed result"}
        )
        if terminal.get("ok") is True:
            try:
                current = api.broker_session_snapshot()
                session_matches = (
                    current.connected is True
                    and current.connection_epoch == expected_epoch
                    and current.login_session_id == expected_session
                )
            except Exception:
                session_matches = False
            if (
                terminal.get("code") != code
                or terminal.get("interval") != interval
                or terminal.get("market_data_identity") != f"{code}_AL"
                or terminal.get("market_source") != "INTEGRATED"
                or terminal.get("display_only") is not True
                or terminal.get("connection_epoch") != expected_epoch
                or terminal.get("login_session_id") != expected_session
                or not session_matches
            ):
                terminal.update(
                    ok=False,
                    reason_code="DISPLAY_TIMEFRAME_RESPONSE_IDENTITY_INVALID",
                )
            else:
                installed = install(
                    code,
                    interval,
                    terminal.get("rows"),
                    as_of=as_of,
                    market_data_identity=terminal.get("market_data_identity"),
                    market_source=terminal.get("market_source"),
                    display_only=terminal.get("display_only"),
                    **({
                        "request_as_of": terminal["request_as_of"],
                        "received_at": terminal["received_at"],
                    } if (interval == 1
                          and isinstance(terminal.get("request_as_of"), str)
                          and isinstance(terminal.get("received_at"), str))
                       else {}),
                    connection_epoch=terminal.get("connection_epoch"),
                    login_session_id=terminal.get("login_session_id"),
                )
                terminal["install_result"] = (
                    dict(installed)
                    if isinstance(installed, dict)
                    else {
                        "ok": False,
                        "reason_code": "DISPLAY_TIMEFRAME_INSTALL_INVALID",
                    }
                )
                if terminal["install_result"].get("ok") is not True:
                    terminal["ok"] = False
                    terminal["reason_code"] = str(
                        terminal["install_result"].get("reason_code")
                        or "DISPLAY_TIMEFRAME_INSTALL_FAILED"
                    )
        if terminal.get("ok") is True and native_plan is not None:
            if native_started is None:
                # Synchronous test adapters can call back before request()
                # returns its immutable request metadata.
                native_terminals.append(dict(terminal))
            else:
                audit_native_receipt(terminal)
        if callable(on_terminal):
            on_terminal(terminal)

    try:
        started = request(
            code,
            name,
            interval=interval,
            count=request_count,
            callback=received,
            nxt_available=True,
        )
    except Exception as exc:
        started = {
            "ok": False,
            "error": str(exc),
            "reason_code": "DISPLAY_TIMEFRAME_REQUEST_FAILED",
        }
    if (
        isinstance(started, dict)
        and started.get("ok") is not True
        and not callback_called
    ):
        received(started)
    return (
        dict(started)
        if isinstance(started, dict)
        else {"ok": False, "error": "malformed start result"}
    )


def _is_refresh_target(stock_dir: Path) -> bool:
    state = read_json_dict(stock_dir / "state.json")
    if state.get("review_required") is True or state.get("trade_enabled") is not True:
        return False
    return str(state.get("status") or "").strip().upper() not in {
        "REVIEW_REQUIRED",
        "REVIEW",
        "EMERGENCY_STOPPED",
        "EMERGENCY_STOP",
        "EMERGENCY",
        "STOPPED",
        "UNREGISTERED",
    }


def _refresh_targets(window: Any) -> list[tuple[Path, str, str]]:
    registered_getter = getattr(window, "registered_operation_targets", None)
    if callable(registered_getter):
        try:
            targets = [
                (Path(stock_dir), str(code), str(name))
                for stock_dir, code, name in registered_getter()
                if str(stock_dir or "").strip()
            ]
        except Exception:
            return []
        return sorted(targets, key=lambda item: item[1])
    targets: list[tuple[Path, str, str]] = []
    for value in all_registered_stock_dirs():
        stock_dir = Path(value)
        if not _is_refresh_target(stock_dir):
            continue
        code, name = parse_stock_folder_name(stock_dir.name)
        if code:
            targets.append((stock_dir, code, name))
    return sorted(targets, key=lambda item: item[1])


def _api_from_host(window: Any) -> Any:
    api = getattr(window, "kiwoom_api", None)
    if api is not None:
        return api
    parent = getattr(window, "parent", None)
    owner = parent() if callable(parent) else None
    return getattr(owner, "kiwoom_api", None)


def _already_bootstrapped(stock_dir: Path, trade_date: str, as_of: datetime) -> bool:
    day_candles = filter_candles_by_trade_date(load_candles(stock_dir), trade_date)
    if not day_candles:
        return False
    times = [
        value
        for candle in day_candles
        if (value := candle_market_datetime(candle)) is not None
    ]
    if not times:
        return False
    if as_of.time() < MARKET_BUCKET_ANCHOR:
        return True
    first = min(times)
    return (first.hour, first.minute) <= (9, 1)


def _has_trade_date_candles(stock_dir: Path, trade_date: str) -> bool:
    return bool(filter_candles_by_trade_date(load_candles(stock_dir), trade_date))


def _rotated_targets(
    window: Any,
    targets: list[tuple[Path, str, str]],
    *,
    minute_key: str = "",
) -> tuple[list[tuple[Path, str, str]], int]:
    if not targets:
        return [], 0
    offset = (
        int(getattr(window, "_candle_refresh_round_robin_offset", 0) or 0)
        % len(targets)
    )
    rotated = targets[offset:] + targets[:offset]
    cost_reader = getattr(window, "operation_candle_request_cost", None)
    selected: list[tuple[Path, str, str]] = []
    planned_requests = 0
    for target in rotated:
        cost = 1
        if minute_key and callable(cost_reader):
            try:
                cost = max(int(cost_reader(target[1], minute_key)), 1)
            except (TypeError, ValueError, OverflowError):
                cost = 1
        if selected and planned_requests + cost > MAX_REQUESTS_PER_CYCLE:
            break
        selected.append(target)
        planned_requests += min(cost, MAX_REQUESTS_PER_CYCLE)
        if planned_requests >= MAX_REQUESTS_PER_CYCLE:
            break
    setattr(
        window,
        "_candle_refresh_round_robin_offset",
        (offset + len(selected)) % len(targets),
    )
    return selected, len(targets) - len(selected)


def refresh_operation_candles(
    window: Any,
    minute_key: str,
    *,
    on_complete: RefreshCompletion | None = None,
    drain_all: bool = False,
) -> dict[str, Any]:
    """Refresh active stocks serially, then continue the minute signal cycle.

    One request is issued per second and at most fifteen per minute-cycle. This
    remains below the OpenAPI+ per-second, per-minute, and per-hour TR limits.
    """
    if getattr(window, "_automatic_candle_refresh_inflight", False):
        if drain_all:
            setattr(window, "_automatic_candle_refresh_rerun_requested", True)
            setattr(
                window,
                "_automatic_candle_refresh_rerun_minute_key",
                str(minute_key or ""),
            )
        if callable(on_complete):
            waiters = getattr(window, "_automatic_candle_refresh_waiters", None)
            if not isinstance(waiters, list):
                waiters = []
                setattr(window, "_automatic_candle_refresh_waiters", waiters)
            waiters.append(on_complete)
        return {
            "accepted": False,
            "completed": False,
            "reason_code": "CANDLE_REFRESH_ALREADY_RUNNING",
        }

    as_of = parse_market_datetime(minute_key) or datetime.now(SEOUL_TIMEZONE)
    trade_date = as_of.date().isoformat()
    all_targets = _refresh_targets(window)
    if drain_all:
        targets = all_targets
        skipped_by_limit = 0
    else:
        targets, skipped_by_limit = _rotated_targets(
            window,
            all_targets,
            minute_key=str(minute_key or ""),
        )
    api = _api_from_host(window)
    request = getattr(api, "request_minute_candles", None)
    available = getattr(api, "is_available", None)
    connected = getattr(api, "is_connected", None)
    unavailable = (
        not callable(request)
        or (callable(available) and available() is not True)
        or (callable(connected) and connected() is not True)
    )

    summary: dict[str, Any] = {
        "accepted": not unavailable and bool(targets),
        "completed": unavailable or not targets,
        "reason_code": "CANDLE_REFRESH_UNAVAILABLE" if unavailable else (
            "CANDLE_REFRESH_NO_TARGETS" if not targets else "CANDLE_REFRESH_STARTED"
        ),
        "minute_key": minute_key,
        "trade_date": trade_date,
        "target_count": len(targets),
        "skipped_by_limit": skipped_by_limit,
        "requested": 0,
        "succeeded": 0,
        "failed": 0,
        "bootstrap_requested": 0,
        "incremental_requested": 0,
        "realtime_skipped": 0,
        "reconciliation_requested": 0,
        "direct_requested": 0,
        "direct_succeeded": 0,
        "direct_failed": 0,
        "direct_deferred": 0,
        "display_requested": 0,
        "display_succeeded": 0,
        "display_failed": 0,
        "display_deferred": 0,
        "warmup_limit_exceeded": 0,
        "fail_closed": [],
        "max_requests_per_cycle": MAX_REQUESTS_PER_CYCLE,
        "request_spacing_ms": REQUEST_SPACING_MS,
        "drain_all": bool(drain_all),
        "bootstrap_batch_pause_ms": BOOTSTRAP_BATCH_PAUSE_MS if drain_all else 0,
    }

    if unavailable or not targets:
        if callable(on_complete):
            on_complete(dict(summary))
        return summary

    tracker = getattr(window, "_candle_bootstrap_completed", None)
    if not isinstance(tracker, dict):
        tracker = {}
        setattr(window, "_candle_bootstrap_completed", tracker)
    completed_codes = tracker.setdefault(trade_date, set())
    if not isinstance(completed_codes, set):
        completed_codes = set(completed_codes)
        tracker[trade_date] = completed_codes

    refresh_generation = int(
        getattr(window, "_automatic_candle_refresh_generation", 0) or 0
    ) + 1
    setattr(window, "_automatic_candle_refresh_generation", refresh_generation)
    setattr(window, "_automatic_candle_refresh_inflight", True)

    def refresh_is_current() -> bool:
        return (
            int(getattr(window, "_automatic_candle_refresh_generation", 0) or 0)
            == refresh_generation
        )

    def finish() -> None:
        if not refresh_is_current():
            return
        waiters = getattr(window, "_automatic_candle_refresh_waiters", None)
        pending_waiters = list(waiters) if isinstance(waiters, list) else []
        completion_callbacks = (
            ([on_complete] if callable(on_complete) else []) + pending_waiters
        )
        setattr(window, "_automatic_candle_refresh_waiters", [])
        setattr(window, "_automatic_candle_refresh_inflight", False)
        summary["completed"] = True
        summary["reason_code"] = "CANDLE_REFRESH_COMPLETED"
        if getattr(window, "_automatic_candle_refresh_rerun_requested", False):
            rerun_minute_key = str(
                getattr(window, "_automatic_candle_refresh_rerun_minute_key", "")
                or minute_key
            )
            setattr(window, "_automatic_candle_refresh_rerun_requested", False)
            setattr(window, "_automatic_candle_refresh_rerun_minute_key", "")

            def complete_rerun(result: dict[str, Any]) -> None:
                for callback in completion_callbacks:
                    callback(dict(result))

            refresh_operation_candles(
                window,
                rerun_minute_key,
                on_complete=complete_rerun if completion_callbacks else None,
                drain_all=True,
            )
            return
        for callback in completion_callbacks:
            callback(dict(summary))

    def schedule_after_request(
        callback: Callable[[], None],
        *,
        has_more: bool,
    ) -> None:
        delay_ms = REQUEST_SPACING_MS
        if (
            drain_all
            and has_more
            and summary["requested"] > 0
            and summary["requested"] % MAX_REQUESTS_PER_CYCLE == 0
        ):
            delay_ms = BOOTSTRAP_BATCH_PAUSE_MS
        QTimer.singleShot(delay_ms, callback)

    def direct_specs_for(code: str) -> list[dict[str, int]]:
        reader = getattr(window, "direct_timeframe_refresh_specs", None)
        if not callable(reader):
            return []
        try:
            raw = reader(code, minute_key)
        except Exception as exc:
            summary["fail_closed"].append(
                {
                    "stock_code": code,
                    "reason": "DIRECT_TIMEFRAME_SPEC_FAILED",
                    "error": str(exc),
                }
            )
            return []
        return [
            dict(item)
            for item in raw
            if isinstance(item, dict)
            and int(item.get("timeframe_minutes") or 0) > 1
            and int(item.get("request_count") or 0) > 0
        ]

    def display_specs_for(code: str) -> list[dict[str, Any]]:
        reader = getattr(window, "display_timeframe_refresh_specs", None)
        if not callable(reader):
            return []
        try:
            raw = reader(code, minute_key)
        except Exception as exc:
            summary["fail_closed"].append(
                {
                    "stock_code": code,
                    "reason": "DISPLAY_TIMEFRAME_SPEC_FAILED",
                    "error": str(exc),
                }
            )
            return []
        return [
            dict(item)
            for item in raw
            if isinstance(item, dict)
            and int(item.get("timeframe_minutes") or 0) > 0
            and int(item.get("request_count") or 0) > 0
            and item.get("nxt_available") is True
        ]

    def request_next(index: int) -> None:
        if not refresh_is_current():
            return
        if index >= len(targets):
            finish()
            return
        if (
            not drain_all
            and int(summary["requested"]) >= MAX_REQUESTS_PER_CYCLE
        ):
            finish()
            return

        stock_dir, code, name = targets[index]
        direct_specs = direct_specs_for(code)
        display_specs = display_specs_for(code)
        decision = {
            "decision": "TR_PRIMARY_REFRESH",
            "request_kind": NORMAL_TR_REFRESH,
        }
        decide = getattr(window, "market_data_refresh_decision", None)
        if callable(decide):
            candidate = decide(code, minute_key)
            if isinstance(candidate, dict):
                decision = candidate

        m1_request_required = True
        if decision.get("decision") == REALTIME_PRIMARY_SKIP:
            summary["realtime_skipped"] += 1
            m1_request_required = False
        elif (
            decision.get("decision") == REALTIME_RECONCILIATION
            and decision.get("request_required") is False
        ):
            summary["realtime_skipped"] += 1
            m1_request_required = False

        remaining_budget = (
            DEFAULT_CANDLES_MAX_COUNT
            if drain_all
            else max(
                MAX_REQUESTS_PER_CYCLE - int(summary["requested"]),
                0,
            )
        )
        if m1_request_required and remaining_budget <= 0:
            finish()
            return
        direct_budget = (
            len(direct_specs)
            if drain_all
            else max(remaining_budget - (1 if m1_request_required else 0), 0)
        )
        selected_direct_specs = direct_specs[:direct_budget]
        summary["direct_deferred"] += max(
            len(direct_specs) - len(selected_direct_specs),
            0,
        )
        remaining_after_direct = (
            len(display_specs)
            if drain_all
            else max(
                direct_budget - len(selected_direct_specs),
                0,
            )
        )
        selected_display_specs = display_specs[:remaining_after_direct]
        summary["display_deferred"] += max(
            len(display_specs) - len(selected_display_specs),
            0,
        )

        def request_display_at(position: int) -> None:
            if not refresh_is_current():
                return
            if position >= len(selected_display_specs):
                QTimer.singleShot(0, lambda: request_next(index + 1))
                return
            spec = selected_display_specs[position]
            try:
                interval = int(spec["timeframe_minutes"])
                request_count = int(spec["request_count"])
            except (KeyError, TypeError, ValueError):
                summary["display_failed"] += 1
                summary["failed"] += 1
                summary["fail_closed"].append(
                    {
                        "stock_code": code,
                        "reason": "DISPLAY_TIMEFRAME_SPEC_INVALID",
                    }
                )
                QTimer.singleShot(0, lambda: request_display_at(position + 1))
                return

            summary["requested"] += 1
            summary["display_requested"] += 1

            def display_received(result: dict[str, Any]) -> None:
                if not refresh_is_current():
                    return
                if isinstance(result, dict) and result.get("ok") is True:
                    summary["succeeded"] += 1
                    summary["display_succeeded"] += 1
                else:
                    summary["failed"] += 1
                    summary["display_failed"] += 1
                schedule_after_request(
                    lambda: request_display_at(position + 1),
                    has_more=(
                        position + 1 < len(selected_display_specs)
                        or index + 1 < len(targets)
                    ),
                )

            request_display_timeframe_candles_for_stock(
                window,
                code,
                name,
                operation_cycle_minute_key=minute_key,
                timeframe_minutes=interval,
                count=request_count,
                nxt_available=spec.get("nxt_available"),
                on_terminal=display_received,
            )

        def request_direct_at(position: int) -> None:
            if not refresh_is_current():
                return
            if position >= len(selected_direct_specs):
                if selected_display_specs:
                    QTimer.singleShot(0, lambda: request_display_at(0))
                else:
                    QTimer.singleShot(0, lambda: request_next(index + 1))
                return
            spec = selected_direct_specs[position]
            try:
                interval = int(spec["timeframe_minutes"])
                request_count = int(spec["request_count"])
            except (KeyError, TypeError, ValueError):
                summary["direct_failed"] += 1
                summary["failed"] += 1
                summary["fail_closed"].append(
                    {
                        "stock_code": code,
                        "reason": "DIRECT_TIMEFRAME_SPEC_INVALID",
                    }
                )
                QTimer.singleShot(0, lambda: request_direct_at(position + 1))
                return

            summary["requested"] += 1
            summary["direct_requested"] += 1

            def direct_received(result: dict[str, Any]) -> None:
                if not refresh_is_current():
                    return
                if isinstance(result, dict) and result.get("ok") is True:
                    summary["succeeded"] += 1
                    summary["direct_succeeded"] += 1
                else:
                    summary["failed"] += 1
                    summary["direct_failed"] += 1
                has_more = (
                    position + 1 < len(selected_direct_specs)
                    or bool(selected_display_specs)
                    or index + 1 < len(targets)
                )
                schedule_after_request(
                    lambda: request_direct_at(position + 1),
                    has_more=has_more,
                )

            request_direct_timeframe_candles_for_stock(
                window,
                code,
                name,
                operation_cycle_minute_key=minute_key,
                timeframe_minutes=interval,
                count=request_count,
                on_terminal=direct_received,
            )

        if not m1_request_required:
            if selected_direct_specs:
                request_direct_at(0)
            elif selected_display_specs:
                request_display_at(0)
            else:
                QTimer.singleShot(0, lambda: request_next(index + 1))
            return

        reconciliation = decision.get("decision") == REALTIME_RECONCILIATION
        required_count_getter = getattr(
            window,
            "candle_history_required_count",
            None,
        )
        required_count = (
            int(required_count_getter(code))
            if callable(required_count_getter)
            else BOOTSTRAP_REQUEST_COUNT
        )
        if required_count > DEFAULT_CANDLES_MAX_COUNT:
            summary["warmup_limit_exceeded"] += 1
            summary["failed"] += 1
            summary["fail_closed"].append(
                {
                    "stock_code": code,
                    "reason": "CANDLE_WARMUP_LIMIT_EXCEEDED",
                    "required_minute_candles": required_count,
                    "maximum_minute_candles": DEFAULT_CANDLES_MAX_COUNT,
                }
            )
            QTimer.singleShot(0, lambda: request_next(index + 1))
            return

        source_reload_reader = getattr(
            api,
            "production_candle_source_requires_reload",
            None,
        )
        source_reload_required = (
            source_reload_reader(code, name) is True
            if callable(source_reload_reader)
            else False
        )
        stored_count = (
            0
            if source_reload_required
            else len(load_candles(stock_dir))
        )
        bootstrap = (
            source_reload_required
            or stored_count < required_count
            or (
                code not in completed_codes
                and not _already_bootstrapped(
                    stock_dir,
                    trade_date,
                    as_of,
                )
            )
        )
        count = (
            INCREMENTAL_REQUEST_COUNT
            if reconciliation
            else (
                min(
                    max(required_count, 1),
                    DEFAULT_CANDLES_MAX_COUNT,
                )
                if bootstrap
                else INCREMENTAL_REQUEST_COUNT
            )
        )
        summary["requested"] += 1
        if reconciliation:
            summary["reconciliation_requested"] += 1
        else:
            summary[
                "bootstrap_requested"
                if bootstrap
                else "incremental_requested"
            ] += 1

        def received(result: dict[str, Any]) -> None:
            if not refresh_is_current():
                return
            if isinstance(result, dict) and result.get("ok") is True:
                summary["succeeded"] += 1
                if (
                    bootstrap
                    and not reconciliation
                    and _has_trade_date_candles(
                        stock_dir,
                        trade_date,
                    )
                ):
                    completed_codes.add(code)
            else:
                summary["failed"] += 1
            if reconciliation:
                complete_reconciliation = getattr(
                    window,
                    "complete_realtime_reconciliation",
                    None,
                )
                if callable(complete_reconciliation):
                    complete_reconciliation(
                        code,
                        str(
                            decision.get("reconciliation_minute")
                            or ""
                        ),
                        stock_dir,
                        result,
                    )
            has_more = (
                bool(selected_direct_specs)
                or bool(selected_display_specs)
                or index + 1 < len(targets)
            )
            next_callback = (
                (lambda: request_direct_at(0))
                if selected_direct_specs
                else (
                    (lambda: request_display_at(0))
                    if selected_display_specs
                    else (lambda: request_next(index + 1))
                )
            )
            schedule_after_request(
                next_callback,
                has_more=has_more,
            )

        request_operation_candle_for_stock(
            window,
            stock_dir,
            code,
            name,
            operation_cycle_minute_key=minute_key,
            request_kind=str(
                decision.get("request_kind")
                or NORMAL_TR_REFRESH
            ),
            reconciliation_minute=str(
                decision.get("reconciliation_minute")
                or ""
            ),
            count=count,
            on_terminal=received,
        )

    request_next(0)
    return dict(summary)
