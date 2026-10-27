"""Retained native NX PRE history is presentation evidence after AL takeover."""

from copy import deepcopy
from contextlib import nullcontext
from datetime import datetime, timedelta
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import patch

from PyQt5.QtCore import QObject
import pytest

import gui_indicator_follow_signal_validation_v5_flow as v5
import gui_market_data_host as host_module
from gui_market_data_host import MarketDataHost
from tests import test_phase5_nxt_native as native
from tests.test_display_only_integrated_history import _opt10080_row
from tests.test_v5_native_selected_integration_20261026 import Signal
from tests import test_indicator_follow_signal_validation_v5_baseline as baseline
from stock_market_source_plan import initialize_stock_market_source_plan


def _transition(tmp_path, monkeypatch, *, mutate_nx=None, regular_only=False,
                interaction=None, nx_events=None, defer_al_publication=False):
    def extras(now):
        day = now.strftime("%Y%m%d")
        return [dict(time=day + "084500", open=341, high=346, low=340,
                     close=345, volume=50),
                dict(native._forming_row(now, 3), open=346, high=350,
                     low=344, close=348)]

    flow, nx_calls, prepared, failures, clock, broker_session = native._run_nxt(
        tmp_path, monkeypatch, interval=3, history_count=1000,
        request_clock=lambda at: at.replace(minute=49), extra_candles=extras)
    assert not failures and prepared
    window = prepared[0]["window"]
    key = id(window)
    nx_pool = flow._historical_pools[key]
    assert nx_pool["native_forming_candle"]["time"].endswith("084800")
    assert nx_pool["native_forming_candle"]["is_complete"] is False
    nx_before = deepcopy(nx_pool)
    if mutate_nx is not None:
        mutate_nx(nx_pool)
    if regular_only:
        old_run = flow._last_run_requests[key]
        rules = old_run.settings_snapshot.to_dict()
        rules["validation_market_scope"] = {"regular_market_only": True}
        flow._last_run_requests[key] = v5.IndicatorFollowSignalValidationRunRequest(
            v5.ValidationSettingsSnapshot(rules), old_run.candle_count,
            force_historical_refresh=False)
    now = clock[0].replace(hour=9, minute=4, second=6)
    prior_al = [dict(row, time=row["time"][:8] + "09" + row["time"][10:],
                     open=510, high=511, low=509, close=510)
                for row in nx_pool["candles"] if row["time"][:8] < now.strftime("%Y%m%d")]
    for days in range(101, 121):
        for i in range(10):
            at = (now - timedelta(days=days)).replace(
                hour=9, minute=0, second=0) + timedelta(minutes=i * 3)
            prior_al.append(dict(time=at.strftime("%Y%m%d%H%M%S"), open=510,
                                 high=511, low=509, close=510, volume=10))
    prior_al.sort(key=lambda row: row["time"])
    al_candles = [*prior_al, dict(time=prior_al[-1]["time"][:8] + "093300",
                                  open=510, high=511, low=509, close=510, volume=10),
                  dict(time=now.strftime("%Y%m%d") + "090000",
                                  open=510, high=511, low=509, close=510, volume=10)]
    history_calls = []

    def request_al(code, _name, **kwargs):
        history_calls.append((code, kwargs["interval"], kwargs["count"]))
        started = dict(ok=True, code=code, rqname="al-history-1",
                       market_data_identity="005930_AL", market_source="INTEGRATED",
                       display_only=True, connection_epoch=1, login_session_id="NX-SESSION",
                       request_as_of=now.isoformat(), started_at=now.isoformat())
        kwargs["callback"](dict(started, type="minute_candles", interval=3,
                                 request_id="al-history-1", trcode="opt10080",
                                 rows=flow._historical_rows_from_candles(al_candles),
                                 rows_count=len(al_candles), received_at=now.isoformat()))
        return started

    flow._broker.request_minute_candles_display_only_integrated = request_al
    flow._cached_history = lambda *_a, **_k: None
    window._normal_base_ready = True
    window._normal_base_requested = True
    window.marking_display_check = SimpleNamespace(
        isChecked=lambda: interaction == "marking")
    if interaction == "range":
        window.validation_range = ("20261026090000", "20261026090300")
    window.is_chart_interaction_busy = lambda: False
    window._filter_display_descriptors = lambda _rules: []
    window.install_normal_base = lambda *_a, **_k: None
    calculations, displays, signals = [], [], []
    window.set_historical_candle_pool = lambda rows, **_k: calculations.append(deepcopy(rows))
    window.set_historical_display_candles = lambda rows, **_k: displays.append(deepcopy(rows))
    window.set_live_sor_monitor_signal = lambda signal, **_k: signals.append(deepcopy(signal))
    flow._history_targets[key] = 100
    owner = QObject()
    signal = Signal()
    market = SimpleNamespace(
        _v5_observation_leases={key: "005930"}, _v5_observation_timeframes={key: 3},
        _v5_watch_generation=4, _native_observation_forming_by_stock={},
        _native_observation_completed_by_stock={},
        kiwoom_api=SimpleNamespace(broker_session_snapshot=lambda: broker_session[0]),
        market_data_observed=signal)
    market.native_selected_observation_snapshot = lambda *args: (
        MarketDataHost.native_selected_observation_snapshot(market, *args))
    owner.existing_market_data_host = lambda: market
    flow.setParent(owner)
    flow._fixture_owner = owner
    flow._v5_lease_hosts[key] = market
    flow._bind_sor_m1_observations()
    monkeypatch.setattr(host_module, "PROJECT_ROOT", tmp_path)
    if nx_events is not None:
        flow._history_targets[key] = 1
        nx_events(flow, market, window, clock)
        nx_pool = flow._historical_pools[key]
        flow._history_targets[key] = 100
    clock[0] = now
    requested = now.replace(second=1)
    rows = [_opt10080_row(now.strftime("%Y%m%d") + "090300", 511),
            _opt10080_row(now.strftime("%Y%m%d") + "090000", 505)]
    started = dict(ok=True, code="005930", rqname="al-native-1",
                   market_data_identity="005930_AL", market_source="INTEGRATED",
                   display_only=True, connection_epoch=1, login_session_id="NX-SESSION",
                   request_as_of=requested.isoformat(), started_at=requested.isoformat())
    response = dict(started, interval=3, request_id="al-native-1", rows=rows,
                    received_at=now.isoformat())
    monkeypatch.setattr(host_module, "PROJECT_ROOT", tmp_path)
    deferred = []
    publication = (patch.object(flow, "_emit_signal_scan_completed",
                                side_effect=deferred.append)
                   if defer_al_publication else nullcontext())
    with (publication,
          patch.object(v5, "Thread", side_effect=lambda **kwargs: SimpleNamespace(
            start=kwargs["target"])),
          patch.object(v5, "signal_validation_trace_enabled", return_value=False),
          patch.object(v5, "trace_signal_validation_event"),
          patch.object(v5, "_trace_sor_al_m1"),
          patch.object(flow, "_ensure_integrated_display_sidecar")):
        result = MarketDataHost.install_native_observation_candle(
            market, "005930", 3, rows, source="INTEGRATED", request_as_of=requested,
            received_at=now, identity=(1, "NX-SESSION"), request_id="al-native-1",
            now=now, request_receipt=(started, response))
    assert result["ok"] is True
    return SimpleNamespace(flow=flow, window=window, nx_pool=nx_pool,
                           nx_before=nx_before, calculations=calculations,
                           displays=displays, signals=signals, history_calls=history_calls,
                           nx_calls=nx_calls, clock=clock, broker_session=broker_session,
                            events=signal.events, market=market, deferred=deferred)


def test_transition_publishes_verified_nx_pre_on_al_display_only(tmp_path, monkeypatch):
    case = _transition(tmp_path, monkeypatch)
    assert len(case.history_calls) == 1, (
        case.history_calls, len(case.flow._historical_pools[id(case.window)]["candles"]),
        case.flow._historical_pools[id(case.window)].get("requested_count"))
    assert len(case.nx_calls) == 1
    assert not case.flow._offsession_sor_m1_requests
    assert case.history_calls[0][1] == 3
    assert case.nx_pool == case.nx_before
    assert case.calculations and case.displays and len(case.signals) == 1, case.window.errors
    display = case.displays[-1]
    calc = case.calculations[-1]
    retained = [row for row in display if row["time"].endswith("084500")]
    assert len(retained) == 1 and retained[0]["close"] == 345
    assert retained[0]["market_source"] == "NXT"
    assert retained[0]["market_data_identity"] == "005930_NX"
    assert any(row["time"].endswith("090000") for row in display)
    assert all(not row["time"].endswith("084800") for row in display)
    assert all(not "0850" <= row["time"][8:12] < "0900" for row in display)
    assert len({row["time"] for row in display}) == len(display)
    assert all(not row["time"].endswith("084500") for row in calc)
    assert case.flow._historical_pools[id(case.window)]["market_source"] == "INTEGRATED"
    assert case.signals[-1].get("unavailable") is not True


@pytest.mark.parametrize("tamper", [
    lambda pool: pool["candles"][-1].update(close=344),
    lambda pool: pool["native_minute_receipt"][1].update(market_data_identity="005930_AL"),
    lambda pool: pool["native_minute_receipt"][1].update(interval=5),
    lambda pool: pool.update(native_minute_receipt=None, verified_nxt_eligible=True),
])
def test_tampered_nx_pool_or_receipt_never_enters_al_display(tmp_path, monkeypatch, tamper):
    case = _transition(tmp_path, monkeypatch, mutate_nx=tamper)
    assert case.displays and case.signals
    assert all(not row["time"].endswith("084500") for row in case.displays[-1])
    assert case.flow._historical_pools[id(case.window)]["market_source"] == "INTEGRATED"


def test_retained_cache_receipt_is_checked_after_source_changes(tmp_path, monkeypatch):
    case = _transition(tmp_path, monkeypatch)
    flow = case.flow
    session = flow._validation_sessions[id(case.window)]
    old = deepcopy(case.nx_before)
    started, response = old.pop("native_minute_receipt")
    rows = old["candles"]
    old["native_cache_candles"] = tuple(deepcopy(rows))
    old["native_cache_receipt"] = dict(
        stock_code="005930", timeframe_key="M3", market_data_identity="005930_NX",
        market_source="NXT", completed_only=True, request_id=started["rqname"],
        request_as_of=started["request_as_of"], received_at=response["received_at"],
        connection_epoch=1, session_sha256=hashlib.sha256(b"NX-SESSION").hexdigest(),
        source_content_sha256=hashlib.sha256(json.dumps(
            rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False).encode()).hexdigest())
    assert any(row["time"].endswith("084500")
               for row in flow._retained_native_nx_pre_display(session, old))
    old["native_cache_receipt"]["source_content_sha256"] = "0" * 64
    assert flow._retained_native_nx_pre_display(session, old) == ()


def test_valid_completed_cache_subset_keeps_nx_display_after_al(tmp_path, monkeypatch):
    case = _transition(tmp_path, monkeypatch)
    flow = case.flow
    session = flow._validation_sessions[id(case.window)]
    old = deepcopy(case.nx_before)
    started, response = old.pop("native_minute_receipt")
    full = deepcopy(old["candles"])
    old["native_cache_candles"] = tuple(full)
    old["native_cache_receipt"] = dict(
        stock_code="005930", timeframe_key="M3", market_data_identity="005930_NX",
        market_source="NXT", completed_only=True, request_id=started["rqname"],
        request_as_of=started["request_as_of"], received_at=response["received_at"],
        connection_epoch=1, session_sha256=hashlib.sha256(b"NX-SESSION").hexdigest(),
        source_content_sha256=hashlib.sha256(json.dumps(
            full, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False).encode()).hexdigest())
    old["candles"] = deepcopy(full[-2:])
    snapshot = old["snapshot"]
    old["snapshot"] = v5.ValidationHistoricalSnapshot(
        stock=snapshot.stock, timeframe_minutes=3, timeframe_key="M3",
        requested_count=2, request_id=snapshot.request_id,
        rows=flow._historical_rows_from_candles(old["candles"]),
        market_data_identity="005930_NX", market_source="NXT")
    assert any(row["time"].endswith("084500")
               for row in flow._retained_native_nx_pre_display(session, old))


def test_proven_prior_day_cache_survives_current_al_login(tmp_path, monkeypatch):
    prior = datetime.now(v5.SEOUL_TIMEZONE) - timedelta(days=1)
    requested = prior.replace(hour=8, minute=49, second=0, microsecond=0)
    received = requested + timedelta(seconds=1)
    initialize_stock_market_source_plan(
        tmp_path, "005930", "Samsung", trading_day=prior.date(),
        decided_at=requested, nxt_available=True)
    case = _transition(tmp_path, monkeypatch)
    flow = case.flow
    session = flow._validation_sessions[id(case.window)]
    old = deepcopy(case.nx_before)
    old.pop("native_minute_receipt")
    day = prior.strftime("%Y%m%d")
    full = [dict(time=day + "084200", open=340, high=344, low=339,
                 close=343, volume=50),
            dict(time=day + "084500", open=341, high=346, low=340,
                 close=345, volume=50)]
    old["native_cache_candles"] = tuple(deepcopy(full))
    old["candles"] = deepcopy(full[-1:])
    old["native_cache_receipt"] = dict(
        stock_code="005930", timeframe_key="M3", market_data_identity="005930_NX",
        market_source="NXT", completed_only=True, request_id="prior-native",
        request_as_of=requested.isoformat(), received_at=received.isoformat(),
        connection_epoch=2, session_sha256=hashlib.sha256(b"PRIOR-LOGIN").hexdigest(),
        source_content_sha256=hashlib.sha256(json.dumps(
            full, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False).encode()).hexdigest())
    snapshot = old["snapshot"]
    old["snapshot"] = v5.ValidationHistoricalSnapshot(
        stock=snapshot.stock, timeframe_minutes=3, timeframe_key="M3",
        requested_count=1, request_id="VALIDATION_CACHE_RESTORE",
        rows=flow._historical_rows_from_candles(old["candles"]),
        market_data_identity="005930_NX", market_source="NXT")
    retained = flow._retained_native_nx_pre_display(session, old)
    assert [row["close"] for row in retained if row["time"] == day + "084500"] == [345]


def test_native_selected_m1_history_can_continue_on_al_display(tmp_path, monkeypatch):
    flow, calls, prepared, failures, clock, _broker_session = native._run_nxt(
        tmp_path, monkeypatch, interval=1, history_count=10)
    assert not failures and len(prepared) == 1
    assert len(calls) == 1 and calls[0][2] == 1
    session, old = prepared[0]["session"], prepared[0]["pool"]
    clock[0] = clock[0].replace(hour=9, minute=4)
    retained = flow._retained_native_nx_pre_display(session, old)
    assert retained and all(row["market_data_identity"] == "005930_NX" for row in retained)


def test_detached_display_snapshot_rejects_later_content_tamper(tmp_path, monkeypatch):
    case = _transition(tmp_path, monkeypatch)
    flow = case.flow
    key = id(case.window)
    al = flow._historical_pools[key]
    evidence = al["_retained_native_nx_display"]
    target = next(row for row in evidence["rows"] if row["time"].endswith("084500"))
    target["close"] = 344
    view = flow._validation_pool_for_session(flow._validation_sessions[key], al,
                                             target_count=5000)
    assert all(not row["time"].endswith("084500")
               for row in view.get("display_candles", view["candles"]))


def test_original_native_receipt_prices_reject_replaced_pool_and_snapshot(
        tmp_path, monkeypatch):
    def replace_both(pool):
        pool["candles"][-1]["close"] = 344  # Still valid OHLC; receipt prices stay 345.
        old = pool["snapshot"]
        pool["snapshot"] = v5.ValidationHistoricalSnapshot(
            stock=old.stock, timeframe_minutes=old.timeframe_minutes,
            timeframe_key=old.timeframe_key, requested_count=old.requested_count,
            request_id=old.request_id,
            rows=v5.IndicatorFollowSignalValidationV5Flow._historical_rows_from_candles(
                pool["candles"]), market_data_identity=old.market_data_identity,
            market_source=old.market_source)

    case = _transition(tmp_path, monkeypatch, mutate_nx=replace_both)
    assert case.displays
    assert all(not row["time"].endswith("084500") for row in case.displays[-1])


def test_bad_optional_delta_does_not_erase_verified_nx_history(tmp_path, monkeypatch):
    case = _transition(tmp_path, monkeypatch,
                       mutate_nx=lambda pool: pool.update(native_live_snapshot={"bad": True}))
    assert any(row["time"].endswith("084500") and row["close"] == 345
               for row in case.displays[-1])


def test_reconnect_and_wrong_session_reject_retained_evidence(tmp_path, monkeypatch):
    case = _transition(tmp_path, monkeypatch)
    flow = case.flow
    session = flow._validation_sessions[id(case.window)]
    case.broker_session[0] = SimpleNamespace(
        connected=True, connection_epoch=2, login_session_id="RECONNECTED")
    assert flow._retained_native_nx_pre_display(session, case.nx_pool) == ()
    case.broker_session[0] = SimpleNamespace(
        connected=True, connection_epoch=1, login_session_id="NX-SESSION")
    wrong = deepcopy(case.nx_pool)
    wrong["timeframe_key"] = "M5"
    assert flow._retained_native_nx_pre_display(session, wrong) == ()
    case.clock[0] += timedelta(days=1)
    assert flow._retained_native_nx_pre_display(session, case.nx_pool) == ()


@pytest.mark.parametrize("tamper", [False, True])
def test_independently_proven_native_delta_enters_display_only(
        tmp_path, monkeypatch, tamper):
    def add_delta(pool):
        day = pool["candles"][-1]["time"][:8]
        requested = datetime.fromisoformat(
            pool["native_minute_receipt"][0]["request_as_of"]).replace(second=2)
        received = requested.replace(second=3)
        raw = [_opt10080_row(day + "084200", 342)]
        started = dict(ok=True, code="005930", rqname="nx-delta-1",
                       market_data_identity="005930_NX", market_source="NXT",
                       display_only=True, connection_epoch=1, login_session_id="NX-SESSION",
                       request_as_of=requested.isoformat(), started_at=requested.isoformat())
        response = dict(started, interval=3, request_id="nx-delta-1", rows=raw,
                        received_at=received.isoformat())
        row = dict(time=day + "084200", open=342, high=344, low=340, close=343,
                   volume=10, is_complete=True, market_source="NXT",
                   market_data_identity="005930_NX")
        pool["native_live_snapshot"] = dict(request_receipt=(started, response),
            completed_candles=[deepcopy(row)], forming_candle=None, stock_code="005930",
            timeframe_minutes=3, market_source="NXT", market_data_identity="005930_NX",
            request_id="nx-delta-1", session_identity=(1, "NX-SESSION"))
        pool["native_live_completed"] = (deepcopy(row),)
        if tamper:
            pool["native_live_completed"][0]["close"] = 344
            pool["native_live_snapshot"]["completed_candles"][0]["close"] = 344

    case = _transition(tmp_path, monkeypatch, mutate_nx=add_delta)
    stamp = case.clock[0].strftime("%Y%m%d") + "084200"
    found = [row for row in case.displays[-1] if row["time"] == stamp]
    assert bool(found) is not tamper
    if found:
        assert found[0]["close"] == 343
    assert all(row["time"] != stamp for row in case.calculations[-1])


@pytest.mark.parametrize("tamper_earlier_price", [False, True])
def test_two_real_nx_host_callbacks_keep_earlier_completed_delta_after_al(
        tmp_path, monkeypatch, tamper_earlier_price):
    accepted = []

    def send_nx(flow, market, window, clock):
        day = clock[0].strftime("%Y%m%d")
        for suffix, second, minute, price in (("a", 3, 36, 336),
                                              ("b", 5, 39, 339)):
            received = clock[0].replace(second=second)
            requested = received.replace(second=second - 1)
            clock[0] = received
            rows = [_opt10080_row(f"{day}08{minute:02d}00", price)]
            request_id = f"nx-native-{suffix}"
            started = dict(ok=True, code="005930", rqname=request_id,
                           market_data_identity="005930_NX", market_source="NXT",
                           display_only=True, connection_epoch=1,
                           login_session_id="NX-SESSION",
                           request_as_of=requested.isoformat(),
                           started_at=requested.isoformat())
            response = dict(started, interval=3, request_id=request_id,
                            rows=rows, received_at=received.isoformat())
            with (patch.object(v5, "Thread", side_effect=lambda **kwargs:
                    SimpleNamespace(start=kwargs["target"])),
                  patch.object(v5, "signal_validation_trace_enabled", return_value=False),
                  patch.object(v5, "trace_signal_validation_event"),
                  patch.object(flow, "_ensure_integrated_display_sidecar")):
                result = MarketDataHost.install_native_observation_candle(
                    market, "005930", 3, rows, source="NXT",
                    request_as_of=requested, received_at=received,
                    identity=(1, "NX-SESSION"), request_id=request_id,
                    now=received, request_receipt=(started, response))
            accepted.append(result)
            assert result["ok"] is True
        assert flow._historical_pools[id(window)].get("native_live_completed")
        if tamper_earlier_price:
            pool = flow._historical_pools[id(window)]
            pool["native_live_completed"] = tuple(
                dict(row, close=338) if row["time"] == day + "083600" else row
                for row in pool["native_live_completed"])

    case = _transition(tmp_path, monkeypatch, nx_events=send_nx)
    assert len(accepted) == 2
    display = case.displays[-1]
    day = case.clock[0].strftime("%Y%m%d")
    assert [row["close"] for row in display if row["time"] == day + "083600"] == (
        [] if tamper_earlier_price else [337])
    assert [row["close"] for row in display if row["time"] == day + "083900"] == [340]
    assert all(row["time"] not in {day + "083600", day + "083900"}
               for row in case.calculations[-1])


def test_later_al_native_update_keeps_nx_display_without_history_request(
        tmp_path, monkeypatch):
    case = _transition(tmp_path, monkeypatch)
    now = case.clock[0].replace(minute=7)
    case.clock[0] = now
    requested = now.replace(second=1)
    rows = [_opt10080_row(now.strftime("%Y%m%d") + "090600", 520),
            _opt10080_row(now.strftime("%Y%m%d") + "090300", 515)]
    started = dict(ok=True, code="005930", rqname="al-native-2",
                   market_data_identity="005930_AL", market_source="INTEGRATED",
                   display_only=True, connection_epoch=1, login_session_id="NX-SESSION",
                   request_as_of=requested.isoformat(), started_at=requested.isoformat())
    response = dict(started, interval=3, request_id="al-native-2", rows=rows,
                    received_at=now.isoformat())
    with (patch.object(v5, "Thread", side_effect=lambda **kwargs: SimpleNamespace(
            start=kwargs["target"])),
          patch.object(v5, "signal_validation_trace_enabled", return_value=False),
          patch.object(v5, "trace_signal_validation_event"),
          patch.object(v5, "_trace_sor_al_m1")):
        result = MarketDataHost.install_native_observation_candle(
            case.market, "005930", 3, rows, source="INTEGRATED",
            request_as_of=requested, received_at=now, identity=(1, "NX-SESSION"),
            request_id="al-native-2", now=now, request_receipt=(started, response))
    assert result["ok"] is True
    assert case.history_calls[0][1] == 3
    assert case.history_calls[0][2] == 1000
    assert len(case.signals) == 2
    assert any(row["time"].endswith("084500") and row["close"] == 345
               for row in case.displays[-1])
    assert all(not row["time"].endswith("084500") for row in case.calculations[-1])
    assert case.flow._historical_pools[id(case.window)].get("_retained_native_nx_display")


def test_reselection_generation_and_other_window_cannot_reuse_nx_rows(
        tmp_path, monkeypatch):
    case = _transition(tmp_path, monkeypatch)
    flow = case.flow
    key = id(case.window)
    al = flow._historical_pools[key]
    session = flow._validation_sessions[key]
    assert al.get("_retained_native_nx_display")
    flow._request_generation[key] += 1
    installed = []
    with patch.object(flow, "_use_pool_for_window", side_effect=lambda _w, _s, pool, **_k:
                      installed.append(pool)):
        flow._install_pool(case.window, session, al, evaluation_count=1)
    assert installed and "_retained_native_nx_display" not in installed[-1]
    other = SimpleNamespace(stock=v5.ValidationStockRef("000660", "Other"))
    other_key = id(other)
    flow._open_windows[other_key] = other
    flow._historical_pools[other_key] = al
    assert other_key not in flow._native_transition_nx_bindings
    flow._request_generation[other_key] = flow._request_generation[key]
    with patch.object(flow, "_use_pool_for_window", side_effect=lambda _w, _s, pool, **_k:
                      installed.append(pool)):
        flow._install_pool(other, session, al, evaluation_count=1)
    assert "_retained_native_nx_display" not in installed[-1]


def test_shared_same_stock_pool_and_other_session_never_carry_window_nx(
        tmp_path, monkeypatch):
    case = _transition(tmp_path, monkeypatch)
    flow = case.flow
    key = id(case.window)
    al = flow._historical_pools[key]
    session_a = flow._validation_sessions[key]
    with patch.object(flow, "_use_pool_for_window", return_value=True):
        flow._install_pool(case.window, session_a, al, evaluation_count=1)
    shared = v5._SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS[
        flow._shared_pool_key(session_a.request.stock, "M3")]
    assert "_retained_native_nx_display" not in shared
    session_b = v5.ValidationSession(session_a.request)
    view_b = flow._validation_pool_for_session(session_b, shared, target_count=5000)
    assert all(not row["time"].endswith("084500")
               for row in view_b.get("display_candles", view_b["candles"]))
    view_a = flow._validation_pool_for_session(session_a, al, target_count=5000)
    assert any(row["time"].endswith("084500")
               for row in view_a["display_candles"])


@pytest.mark.parametrize("stale", ["reconnect", "generation", "day", "selection"])
def test_deferred_publication_rejects_stale_retained_axis(
        tmp_path, monkeypatch, stale):
    case = _transition(tmp_path, monkeypatch, defer_al_publication=True)
    flow = case.flow
    key = id(case.window)
    assert len(case.deferred) == 1
    payload = case.deferred[0]
    assert "prepared_view_pool" in payload, payload.get("error")
    prepared = payload["prepared_view_pool"]
    assert any(row["time"].endswith("084500") for row in prepared["display_candles"])
    assert flow._retained_native_nx_publication_current(
        case.window, payload["session"], payload["pool"], payload["generation"])
    if stale == "reconnect":
        case.broker_session[0] = SimpleNamespace(
            connected=True, connection_epoch=2, login_session_id="RECONNECTED")
    elif stale == "generation":
        flow._request_generation[key] += 1
    elif stale == "day":
        case.clock[0] += timedelta(days=1)
    else:
        flow._validation_sessions[key] = v5.ValidationSession(payload["session"].request)
    assert not flow._retained_native_nx_publication_current(
        case.window, payload["session"], payload["pool"], payload["generation"])
    flow._on_signal_scan_completed(payload)
    assert not case.displays


@pytest.mark.parametrize("interaction", ["marking", "range"])
def test_transition_defers_publication_during_marking_or_range(
        tmp_path, monkeypatch, interaction):
    case = _transition(tmp_path, monkeypatch, interaction=interaction)
    key = id(case.window)
    assert not case.displays
    assert key in case.flow._native_selected_pending
    assert len(case.history_calls) == 1


def test_duplicate_pre_prefers_proven_nx_and_regular_scope_omits_it(tmp_path, monkeypatch):
    case = _transition(tmp_path, monkeypatch)
    flow = case.flow
    session = flow._validation_sessions[id(case.window)]
    al = flow._historical_pools[id(case.window)]
    view = flow._validation_pool_for_session(session, al, target_count=5000)
    stamp = case.clock[0].strftime("%Y%m%d") + "084500"
    duplicate = dict(time=stamp, open=999, high=1000, low=998, close=999, volume=1)
    mixed = dict(view, display_candles=[duplicate, *view["display_candles"]])
    merged = flow._compose_retained_native_nx_display(session, mixed, 5000)
    assert [row for row in merged["display_candles"] if row["time"] == stamp][0]["close"] == 345
    assert len({row["time"] for row in merged["display_candles"]}) == len(merged["display_candles"])
    regular = _transition(tmp_path / "regular", monkeypatch, regular_only=True)
    assert regular.displays
    assert all(not row["time"].endswith("084500") for row in regular.displays[-1])


@pytest.fixture
def widget_case():
    yield from baseline.case.__wrapped__()


def test_real_widget_retains_5000_axis_and_scroll_with_nx_pre(
        widget_case, tmp_path, monkeypatch):
    case = _transition(tmp_path, monkeypatch)
    flow = case.flow
    session = flow._validation_sessions[id(case.window)]
    al = flow._historical_pools[id(case.window)]
    prior = []
    for i in range(5000):
        at = (case.clock[0] - timedelta(days=i // 70 + 1)).replace(
            hour=9, minute=0, second=0) + timedelta(minutes=(i % 70) * 3)
        prior.append(dict(time=at.strftime("%Y%m%d%H%M%S"), open=510, high=511,
                          low=509, close=510, volume=10))
    view = flow._validation_pool_for_session(session, al, target_count=5000)
    broad = dict(view, display_candles=[*prior, *view["display_candles"]])
    composed = flow._compose_retained_native_nx_display(session, broad, 5000)
    assert len(composed["display_candles"]) == 5000
    assert len({row["time"] for row in composed["display_candles"]}) == 5000
    assert any(row["time"].endswith("084500") and row["close"] == 345
               for row in composed["display_candles"])
    widget = baseline.window_for(widget_case)
    try:
        widget.set_historical_candle_pool(view["candles"], chart_candle_count=5000)
        widget.set_historical_display_candles(
            composed["display_candles"], display_only_integrated=True)
        widget.install_normal_base(session.request.settings_snapshot, "M3")
        widget._set_time_view(1500, 3500)
        assert len(widget._candles) == 5000
        assert widget.visible_candle_span == 3500
        assert widget.time_navigation_scrollbar.maximum() >= 1_500_000
        assert widget._calculation_candles == view["candles"]
    finally:
        widget.close()
