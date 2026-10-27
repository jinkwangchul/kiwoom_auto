"""Offline host-to-V5 native selected-candle integration."""

from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from PyQt5.QtCore import QObject
import pytest

import gui_indicator_follow_signal_validation_v5_flow as v5
import gui_market_data_host as host_module
from candle_timeframe_aggregation import SEOUL_TIMEZONE
from gui_market_data_host import MarketDataHost
from indicator_follow_signal_validation_projection import IndicatorFollowSignalValidationRunRequest
from tests import test_phase5_nxt_native as native
from tests.test_nx_v5_cache_period import _period_run
from tests.test_display_only_integrated_history import _opt10080_row
from tests.test_indicator_follow_signal_validation_v5_integrated_history_scroll import _flow


class Signal:
    def __init__(self):
        self.callbacks = []
        self.events = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, event):
        self.events.append(event)
        for callback in self.callbacks:
            callback(event)


def test_v5_selected_m3_lease_excludes_offsession_m1_prefetch():
    flow = _flow()
    stock = v5.ValidationStockRef("005930", "Samsung")
    window = SimpleNamespace(stock=stock)
    flow._open_windows[id(window)] = window
    rules = v5.ValidationSettingsSnapshot({"bar": {"bar_minutes": 3}})
    flow._last_run_requests[id(window)] = IndicatorFollowSignalValidationRunRequest(rules, 5)
    leases = []
    market = SimpleNamespace(set_v5_observation_lease=lambda *args, **kwargs:
                             leases.append((args, kwargs)) or {"ok": True})
    owner = QObject()
    owner.existing_market_data_host = lambda: market
    flow.setParent(owner)
    flow._fixture_owner = owner
    with patch.object(flow, "_request_offsession_sor_m1_once",
                      side_effect=AssertionError("M1 prefetch")):
        flow._sync_v5_observation_lease(window)
    assert leases == [((id(window), stock.code), {"timeframe_minutes": 3})]


def test_first_m3_forming_crosses_minute_but_expires_at_bar_end_and_sixty_seconds():
    received = datetime(2026, 10, 26, 8, 2, 6, tzinfo=SEOUL_TIMEZONE)
    proof = {"forming_candle": {"is_complete": False,
             "native_bar_end": received.replace(minute=3, second=0).isoformat()},
             "request_receipt": ({"request_as_of": received.replace(
                 minute=1, second=57).isoformat()},
                 {"received_at": received.isoformat()})}
    assert v5._v5_host_native_forming_is_fresh(proof, received)
    assert v5._v5_host_native_forming_is_fresh(
        proof, received.replace(second=59))
    assert not v5._v5_host_native_forming_is_fresh(
        proof, received.replace(minute=3, second=0))
    proof["forming_candle"]["native_bar_end"] = received.replace(
        minute=5, second=0).isoformat()
    assert not v5._v5_host_native_forming_is_fresh(
        proof, received + timedelta(seconds=60))


def test_between_tr_al_tick_reaches_v5_current_filter_without_history_fetch(
        tmp_path, monkeypatch):
    import routines.지표추종매매.routine as routine

    flow, _, prepared, failures, clock, broker_session = native._run_nxt(
        tmp_path, monkeypatch, interval=3, history_count=1000)
    assert not failures and len(prepared) == 1
    window = prepared[0]["window"]
    key = id(window)
    pool = prepared[0]["pool"]
    pool = dict(pool, candles=[dict(row, time=row["time"][:8] + "09" + row["time"][10:])
                               for row in pool["candles"]], market_source="INTEGRATED",
                market_data_identity="005930_AL")
    for field in ("native_cache_receipt", "native_cache_candles",
                  "native_minute_receipt", "native_forming_candle"):
        pool.pop(field, None)
    flow._historical_pools[key] = pool
    flow._validation_nxt_pre_codes.discard("005930")
    before = deepcopy(pool["candles"])
    window._normal_base_ready = window._normal_base_requested = True
    window.marking_display_check = SimpleNamespace(isChecked=lambda: False)
    window.is_chart_interaction_busy = lambda: False
    window._filter_display_descriptors = lambda _rules: []
    window.install_normal_base = lambda *_a, **_k: None
    charts, signals, selections = [], [], []
    window.set_historical_candle_pool = lambda candles, **_k: charts.append(
        [dict(row) for row in candles])
    window.set_historical_display_candles = lambda *_a, **_k: None
    window.set_live_sor_monitor_signal = lambda value, **_k: signals.append(value)
    flow._history_targets[key] = 1
    owner = QObject()
    observed = Signal()
    api = SimpleNamespace(broker_session_snapshot=lambda: broker_session[0],
                          _v5_al_receive_sequence_by_code={"005930": 4},
                          _v5_al_registration={"identity": (1, "NX-SESSION"),
                                               "targets": ("005930",)})
    market = SimpleNamespace(
        _shutting_down=False, _v5_observation_leases={key: "005930"},
        _v5_observation_timeframes={key: 3}, _v5_watch_generation=4,
        _native_observation_forming_by_stock={}, _native_observation_completed_by_stock={},
        _mini_chart_observation_leases={}, _sor_display_live_price_states={},
        _v5_al_native_minutes={}, _v5_al_parity={},
        kiwoom_api=api, market_data_observed=observed,
        _current_display_session_identity=lambda: (1, "NX-SESSION"))
    market.native_selected_observation_snapshot = lambda *args: (
        MarketDataHost.native_selected_observation_snapshot(market, *args))
    owner.existing_market_data_host = lambda: market
    flow.setParent(owner)
    flow._fixture_owner = owner
    flow._v5_lease_hosts[key] = market
    flow._bind_sor_m1_observations()
    at = clock[0].replace(hour=9, minute=1, second=6)
    clock[0] = at
    requested = at - timedelta(seconds=5)
    rows = [_opt10080_row("20261026090000", 111)]
    started = dict(ok=True, code="005930", rqname="al-tick-m3",
                   market_data_identity="005930_AL", market_source="INTEGRATED",
                   display_only=True, connection_epoch=1, login_session_id="NX-SESSION",
                   request_as_of=requested.isoformat(), started_at=requested.isoformat())
    response = dict(started, interval=3, request_id="al-tick-m3", rows=rows,
                    received_at=at.isoformat())
    original_selector = routine.evaluate_current_signal_selection
    def select(candles, rules, context):
        result = original_selector(candles, rules, context)
        selections.append((len(candles), candles[-1]["close"], result))
        return result
    monkeypatch.setattr(host_module, "PROJECT_ROOT", tmp_path)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]
    with patch.object(v5, "Thread", side_effect=lambda **kwargs: SimpleNamespace(
            start=kwargs["target"])), patch.object(flow, "_ensure_integrated_display_sidecar"), \
            patch.object(routine, "evaluate_current_signal_selection", side_effect=select), \
            patch.object(flow._broker, "request_minute_candles_display_only_integrated",
                         side_effect=AssertionError("new history request")):
        installed = MarketDataHost.install_native_observation_candle(
            market, "005930", 3, rows, source="INTEGRATED", request_as_of=requested,
            received_at=at, identity=(1, "NX-SESSION"), request_id="al-tick-m3",
            now=at, request_receipt=(started, response), tick_anchor_sequence=4)
        assert installed["ok"] is True, installed
        first = market.native_selected_observation_snapshot(key, "005930", 3, "al-tick-m3")
        original_batches = flow._historical_pools[key]["native_live_verified_batches"]
        tick_at = at + timedelta(seconds=1)
        clock[0] = tick_at
        tick = dict(stock_code="005930", broker_market_identity="005930_AL",
                    market_source="SOR", v5_only=True, connection_epoch=1,
                    login_session_id="NX-SESSION", market_datetime=tick_at.isoformat(),
                    received_at=tick_at.isoformat(), current_price=150,
                    trade_volume_abs=3, cumulative_volume=103, receive_sequence=5)
        api._v5_al_receive_sequence_by_code["005930"] = 5
        with patch("gui_market_data_host.datetime", Clock):
            MarketDataHost._on_v5_al_tick_received(market, tick)
    second = market.native_selected_observation_snapshot(key, "005930", 3, "al-tick-m3")
    assert second["forming_candle"]["close"] == 150
    assert second["forming_candle"]["volume"] == first["forming_candle"]["volume"] + 3
    assert second["request_receipt"] == first["request_receipt"]
    assert flow._historical_pools[key]["native_live_verified_batches"] == original_batches
    assert len(charts) == len(signals) == 2
    assert charts[-1][-1]["close"] == 150
    assert selections[-1][1] == 150
    assert signals[-1].get("unavailable") is not True
    assert signals[-1]["signal"] == (selections[-1][2]["signal"]
                                    if selections[-1][2]["signal"] in {"BUY", "SELL"} else None)
    assert pool["candles"] == before
    active_pool = flow._historical_pools[key]
    session = flow._validation_sessions[key]
    generation = flow._request_generation.get(key, 0)
    assert flow._live_native_snapshot_is_current(
        window, session, active_pool, "al-tick-m3", generation)
    window.is_chart_interaction_busy = lambda: True
    later = tick_at + timedelta(seconds=1)
    clock[0] = later
    api._v5_al_receive_sequence_by_code["005930"] = 6
    with patch("gui_market_data_host.datetime", Clock):
        MarketDataHost._on_v5_al_tick_received(market, dict(
            tick, market_datetime=later.isoformat(), received_at=later.isoformat(),
            receive_sequence=6, current_price=151, trade_volume_abs=2,
            cumulative_volume=105))
    assert key in flow._native_selected_pending
    api._v5_al_receive_sequence_by_code["005930"] = 7  # dropped normalization callback
    assert market.native_selected_observation_snapshot(key, "005930", 3, "al-tick-m3") is None
    assert not flow._live_native_snapshot_is_current(
        window, session, active_pool, "al-tick-m3", generation)
    window.is_chart_interaction_busy = lambda: False
    flow._publish_pending_native_selected(key)
    assert key not in flow._native_selected_pending
    assert len(signals) == 2


@pytest.mark.parametrize("hour,minute,after_hour,after_minute,source", [
    (8, 49, 8, 50, "NXT"), (8, 49, 9, 0, "NXT"),
    (15, 19, 15, 20, "INTEGRATED"),
    (15, 29, 15, 30, "INTEGRATED"),
    (15, 39, 15, 40, "INTEGRATED"),
])
def test_previous_native_session_proof_rejected_at_source_boundary(
        tmp_path, monkeypatch, hour, minute, after_hour, after_minute, source):
    requested = datetime(2026, 10, 26, hour, minute, 57, tzinfo=SEOUL_TIMEZONE)
    received = requested.replace(second=59)
    current = received.replace(hour=after_hour, minute=after_minute, second=6)
    native.initialize_stock_market_source_plan(
        tmp_path, "005930", "Samsung", trading_day=requested.date(),
        decided_at=requested.replace(hour=8, minute=0), nxt_available=True)
    monkeypatch.setattr(v5, "PROJECT_ROOT", tmp_path)
    identity = "005930_NX" if source == "NXT" else "005930_AL"
    started = dict(ok=True, code="005930", rqname="old-source",
                   market_data_identity=identity, market_source=source,
                   display_only=True, connection_epoch=1, login_session_id="offline",
                   request_as_of=requested.isoformat(), started_at=requested.isoformat())
    response = dict(started, interval=3, request_id="old-source", rows=[],
                    received_at=received.isoformat())
    proof = dict(request_receipt=(started, response), market_source=source,
                 market_data_identity=identity, timeframe_minutes=3,
                 session_identity=(1, "offline"), stock_code="005930",
                 request_id="old-source")
    broker = SimpleNamespace(broker_session_snapshot=lambda: SimpleNamespace(
        connected=True, connection_epoch=1, login_session_id="offline"))
    stock = v5.ValidationStockRef("005930", "Samsung")
    assert not v5._v5_host_native_proof_is_current(stock, proof, broker, current)


@pytest.mark.parametrize("source,minutes,hour,forming_minute,completed_minute", [
    ("NXT", 3, 8, 39, 36),
    ("INTEGRATED", 5, 9, 40, 35),
    ("KRX", 5, 9, 40, 35),
])
@pytest.mark.parametrize("review_case", [
    "base", "marking", "legacy_receipt", "range_late", "replay_late", "busy_late",
])
def test_actual_host_native_selected_event_reaches_final_publication(
        tmp_path, monkeypatch, source, minutes, hour, forming_minute,
        completed_minute, review_case):
    from routines.지표추종매매 import routine

    flow, _, prepared, failures, clock, broker_session = native._run_nxt(
        tmp_path, monkeypatch, source="KRX" if source == "KRX" else "NXT",
        interval=minutes, history_count=1000)
    assert not failures and len(prepared) == 1
    window = prepared[0]["window"]
    session, historical = prepared[0]["session"], prepared[0]["pool"]
    key = id(window)
    if source == "KRX":
        assert flow._validation_source_by_code["005930"] is False
        assert "005930" in flow._validation_frozen_krx_codes
    if source == "INTEGRATED":
        historical = dict(historical, candles=[
            dict(row, time=row["time"][:8] + "09" + row["time"][10:])
            for row in historical["candles"]],
            market_source="INTEGRATED", market_data_identity="005930_AL")
        for field in ("native_cache_receipt", "native_cache_candles",
                      "native_minute_receipt", "native_forming_candle"):
            historical.pop(field, None)
        flow._historical_pools[key] = historical
        flow._validation_nxt_pre_codes.discard("005930")
    historical_before = deepcopy(historical["candles"])
    window._normal_base_ready = True
    window._normal_base_requested = True
    window.marking_display_check = SimpleNamespace(isChecked=lambda: False)
    window.is_chart_interaction_busy = lambda: False
    window._filter_display_descriptors = lambda _rules: []
    window.install_normal_base = lambda *_args, **_kwargs: None
    published_candles = []
    published_signals = []
    window.set_historical_candle_pool = lambda candles, **_kwargs: published_candles.append(
        [dict(row) for row in candles])
    window.set_historical_display_candles = lambda *_args, **_kwargs: None
    window.set_live_sor_monitor_signal = lambda signal, **_kwargs: published_signals.append(signal)
    flow._history_targets[key] = 1
    owner = QObject()
    signal = Signal()
    market = SimpleNamespace(
        _v5_observation_leases={key: "005930"},
        _v5_observation_timeframes={key: minutes},
        _v5_watch_generation=4,
        _native_observation_forming_by_stock={},
        _native_observation_completed_by_stock={},
        kiwoom_api=SimpleNamespace(broker_session_snapshot=lambda: broker_session[0]),
        market_data_observed=signal,
    )
    if review_case == "tick":
        registration = SimpleNamespace(
            active=True, connection_epoch=1, login_session_id="NX-SESSION",
            broker_code_identities=("005930_NX" if source == "NXT" else "005930",))
        if source == "KRX":
            registration.target_stock_codes = ("005930",)
            market.kiwoom_api._realtime_shadow_registration = registration
        else:
            market.kiwoom_api._nxt_display_registration = registration
        market.kiwoom_api._native_receive_sequence_by_code = {"NXT": {}, "KRX": {}}
    market.native_selected_observation_snapshot = lambda *args: (
        MarketDataHost.native_selected_observation_snapshot(market, *args))
    owner.existing_market_data_host = lambda: market
    flow.setParent(owner)
    flow._fixture_owner = owner
    flow._v5_lease_hosts[key] = market
    flow._bind_sor_m1_observations()
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]
    now = clock[0].replace(hour=hour, minute=41, second=6)
    if review_case == "tick":
        now = Clock.fromisoformat(now.isoformat())
    clock[0] = now
    requested = now.replace(minute=40, second=57)
    rows = [_opt10080_row(f"20261026{hour:02d}{forming_minute:02d}00", 111),
            _opt10080_row(f"20261026{hour:02d}{completed_minute:02d}00", 105)]
    request_id = f"native-m{minutes}-1"
    market_identity = ("005930" if source == "KRX" else
                       f"005930_{'NX' if source == 'NXT' else 'AL'}")
    started = dict(ok=True, code="005930", rqname="native-m3-1",
                   market_data_identity=market_identity, market_source=source,
                   display_only=True, connection_epoch=1,
                   login_session_id="NX-SESSION", request_as_of=requested.isoformat(),
                   started_at=requested.isoformat())
    started["rqname"] = request_id
    response = dict(started, interval=minutes, request_id=request_id, rows=rows,
                    received_at=now.isoformat())
    monkeypatch.setattr(host_module, "PROJECT_ROOT", tmp_path)
    deferred, targets = [False], []
    selected_closes = []
    original_select = routine.evaluate_current_signal_selection

    def select(candles, rules, context):
        selected_closes.append(candles[-1]["close"])
        return original_select(candles, rules, context)

    def worker(**kwargs):
        def start():
            if deferred[0]:
                targets.append(kwargs["target"])
            else:
                kwargs["target"]()
        return SimpleNamespace(start=start)

    with patch.object(routine, "evaluate_current_signal_selection", side_effect=select), \
            patch.object(v5, "Thread", side_effect=worker), patch.object(
            flow, "_ensure_integrated_display_sidecar"), patch.object(
            host_module, "datetime", Clock if review_case == "tick" else datetime):
        result = MarketDataHost.install_native_observation_candle(
            market, "005930", minutes, rows, source=source, request_as_of=requested,
            received_at=now, identity=(1, "NX-SESSION"), request_id=request_id,
            now=now, request_receipt=(started, response),
            tick_anchor_sequence=0 if review_case == "tick" else None)
        assert result["ok"] is True
        proof = market.native_selected_observation_snapshot(key, "005930", minutes, request_id)
        assert proof is not None
        assert v5._v5_host_native_proof_is_current(session.request.stock, proof,
                                                    flow._broker, clock[0])
        assert flow._validation_sessions[key] is session
        assert flow._historical_pools[key] is not historical
        assert signal.callbacks
        assert not flow._native_selected_pending
        assert not window.errors, window.errors
        assert len(published_candles) == 1
        assert published_candles[0][-1]["time"] == f"20261026{hour:02d}{forming_minute:02d}00"
        assert published_candles[0][-1]["is_complete"] is False
        assert any(row["time"] == f"20261026{hour:02d}{completed_minute:02d}00"
                   for row in published_candles[0])
        assert len(published_signals) == 1
        assert published_signals[0]["timeframe_key"] == f"M{minutes}"
        assert published_signals[0].get("unavailable") is not True
        assert "signal" in published_signals[0]
        assert historical["candles"] == historical_before
        if review_case == "tick":
            tick_at = now.replace(second=7)
            clock[0] = tick_at
            market.kiwoom_api._native_receive_sequence_by_code[source]["005930"] = 1
            tick = dict(stock_code="005930", canonical_stock_code="005930",
                broker_code_identity=market_identity, market_source=source,
                market_datetime=tick_at.isoformat(), received_at=tick_at.isoformat(),
                receive_sequence=1, native_receive_sequence=1, current_price=150,
                execution_quantity=3, trade_volume_abs=3, cumulative_volume=103)
            MarketDataHost._extend_native_selected_al_forming(market, tick)
            revised = market.native_selected_observation_snapshot(key, "005930", minutes, request_id)
            assert revised and revised["tick_revision"] == 1
            if source == "KRX":
                assert revised["forming_candle"]["snapshot_source"] == (
                    "BROKER_KRX_TR_PLUS_CONTIGUOUS_TICKS")
            assert revised["tr_forming_candle"]["close"] == proof["forming_candle"]["close"]
            assert revised["forming_candle"]["close"] == 150
            assert len(published_candles) == 2
            assert published_candles[-1][-1]["close"] == 150
            assert len(published_signals) == 2
            assert published_signals[-1].get("unavailable") is not True
            assert "signal" in published_signals[-1]
            if source == "KRX":
                assert selected_closes[-1] == 150
            assert historical["candles"] == historical_before
            if source == "KRX":
                stock = session.request.stock
                valid_plan = v5.read_stock_market_source_plan(
                    tmp_path, stock.code, clock[0].date())
                for changed_plan in (
                    dict(valid_plan, nxt_available=True),
                    dict(valid_plan, sessions=[dict(valid_plan["sessions"][0], source="SOR")]),
                    dict(valid_plan, sessions=[dict(valid_plan["sessions"][0],
                                                    market_data_identity="005930_AL")]),
                ):
                    with patch.object(v5, "read_stock_market_source_plan",
                                      return_value=changed_plan):
                        assert not v5._v5_host_native_proof_is_current(
                            stock, revised, flow._broker, clock[0])
                for field, value in (("market_data_identity", "005930_AL"),
                                     ("session_identity", (2, "NX-SESSION")),
                                     ("tick_revision", False)):
                    invalid = deepcopy(revised)
                    invalid[field] = value
                    assert not v5._v5_host_native_proof_is_current(
                        stock, invalid, flow._broker, clock[0])
                for field, value in (("rqname", "other-request"),
                                     ("connection_epoch", 2)):
                    invalid = deepcopy(revised)
                    invalid["request_receipt"][1][field] = value
                    assert not v5._v5_host_native_proof_is_current(
                        stock, invalid, flow._broker, clock[0])
                invalid = deepcopy(revised)
                invalid["forming_candle"]["snapshot_source"] = (
                    "BROKER_AL_TR_PLUS_CONTIGUOUS_TICKS")
                assert not v5._v5_host_native_proof_is_current(
                    stock, invalid, flow._broker, clock[0])
                registration.active = False
                assert market.native_selected_observation_snapshot(
                    key, "005930", minutes, request_id) is None
                registration.active = True
                assert market.native_selected_observation_snapshot(
                    key, "005930", minutes, request_id) == revised
                event = signal.events[-1]
                flow._native_selected_last.pop(key, None)
                flow._historical_pools[key] = dict(
                    flow._historical_pools[key], native_live_snapshot=None)
                publications = []
                with patch.object(flow, "_publish_pending_native_selected",
                                  side_effect=publications.append):
                    flow._validation_source_by_code.pop("005930")
                    flow._handle_native_selected_snapshot_accepted(event)
                    flow._validation_source_by_code["005930"] = False
                    flow._validation_frozen_krx_codes.remove("005930")
                    flow._handle_native_selected_snapshot_accepted(event)
                    flow._validation_frozen_krx_codes.add("005930")
                    flow._validation_source_by_code["005930"] = True
                    flow._handle_native_selected_snapshot_accepted(event)
                    assert not publications and key not in flow._native_selected_pending
                    flow._validation_source_by_code["005930"] = False
                    flow._handle_native_selected_snapshot_accepted(event)
                    assert publications == [key]
                invalid_tick = dict(tick, native_receive_sequence=3,
                                    receive_sequence=3, current_price=151)
                MarketDataHost._extend_native_selected_al_forming(market, invalid_tick)
                assert market.native_selected_observation_snapshot(
                    key, "005930", minutes, request_id) is None
            return
        if source == "INTEGRATED":
            assert all(row["time"][8:10] == "09" for row in historical["candles"])
        if review_case == "marking":
            disabled, tooltips, applied = [], [], []
            window._marking_generation = 1
            window._marking_key = ("offline-native-mark",)
            window.marking_display_check = SimpleNamespace(
                isChecked=lambda: True, setToolTip=tooltips.append)
            window._disable_marking = lambda **kwargs: disabled.append(kwargs)
            window.apply_marking_snapshot = lambda *args: applied.append(args) or True
            calc = published_candles[0]
            marking_request = {
                "settings": session.request.settings_snapshot,
                "generation": 1, "key": window._marking_key,
                "calculation": calc, "display": [calc[-1]],
                "times": (calc[-1]["time"], calc[-1]["time"]),
                "range": (len(calc) - 1, len(calc) - 1),
                "mixed": False, "descriptors": (),
            }
            with patch.object(v5, "_trace_v5_marking"):
                flow._start_marking(window, marking_request)
            assert not disabled, tooltips
            assert applied, tooltips
            return
        if review_case == "legacy_receipt":
            deferred[0] = True
            pending_at = now.replace(second=10)
            pending_id = f"native-m{minutes}-pending"
            pending_started = dict(started, rqname=pending_id,
                                   request_as_of=pending_at.replace(second=9).isoformat(),
                                   started_at=pending_at.replace(second=9).isoformat())
            pending_response = dict(pending_started, interval=minutes,
                                    request_id=pending_id, rows=rows,
                                    received_at=pending_at.isoformat())
            clock[0] = pending_at
            accepted = MarketDataHost.install_native_observation_candle(
                market, "005930", minutes, rows, source=source,
                request_as_of=pending_at.replace(second=9), received_at=pending_at,
                identity=(1, "NX-SESSION"), request_id=pending_id, now=pending_at,
                request_receipt=(pending_started, pending_response))
            assert accepted["ok"] is True and len(targets) == 1
            later = now.replace(second=12)
            clock[0] = later
            legacy_id = f"native-m{minutes}-legacy"
            accepted = MarketDataHost.install_native_observation_candle(
                market, "005930", minutes, rows, source=source,
                request_as_of=later.replace(second=11), received_at=later,
                identity=(1, "NX-SESSION"), request_id=legacy_id, now=later,
                request_receipt=None)
            assert accepted["ok"] is True
            assert market.native_selected_observation_snapshot(
                key, "005930", minutes, pending_id) is None
            targets.pop(0)()
            assert len(published_candles) == 1
            assert len(published_signals) == 1
            return
        signal.emit({"source": "NATIVE_SELECTED_FORMING_ACCEPTED",
                     "stock_code": "005930", "timeframe_minutes": minutes,
                     "request_id": request_id})
        assert len(published_candles) == 1
        deferred[0] = True
        for suffix, second, price in (("2", 10, 112), ("3", 12, 113)):
            clock[0] = now.replace(second=second)
            requested_next = clock[0].replace(second=second - 1)
            next_id = f"native-m{minutes}-{suffix}"
            next_rows = [_opt10080_row(
                f"20261026{hour:02d}{forming_minute:02d}00", price), rows[1]]
            next_started = dict(started, rqname=next_id,
                                request_as_of=requested_next.isoformat(),
                                started_at=requested_next.isoformat())
            next_response = dict(next_started, interval=minutes,
                                 request_id=next_id, rows=next_rows,
                                 received_at=clock[0].isoformat())
            accepted = MarketDataHost.install_native_observation_candle(
                market, "005930", minutes, next_rows, source=source,
                request_as_of=requested_next, received_at=clock[0],
                identity=(1, "NX-SESSION"), request_id=next_id, now=clock[0],
                request_receipt=(next_started, next_response))
            assert accepted["ok"] is True
        assert len(targets) == 1
        targets.pop(0)()
        assert len(published_candles) == 1
        assert len(published_signals) == 1
        assert len(targets) == 1
        targets.pop(0)()
        assert len(published_candles) == 2
        assert published_candles[-1][-1]["close"] == 114
        assert len(published_signals) == 2
        busy = [True]
        window.is_chart_interaction_busy = lambda: busy[0]
        for suffix, second, price, earlier_minute in (
                ("4", 14, 114, completed_minute - minutes),
                ("5", 16, 115, completed_minute)):
            clock[0] = now.replace(second=second)
            requested_next = clock[0].replace(second=second - 1)
            next_id = f"native-m{minutes}-{suffix}"
            next_rows = [_opt10080_row(
                f"20261026{hour:02d}{forming_minute:02d}00", price),
                _opt10080_row(f"20261026{hour:02d}{earlier_minute:02d}00", 106)]
            next_started = dict(started, rqname=next_id,
                                request_as_of=requested_next.isoformat(),
                                started_at=requested_next.isoformat())
            next_response = dict(next_started, interval=minutes,
                                 request_id=next_id, rows=next_rows,
                                 received_at=clock[0].isoformat())
            accepted = MarketDataHost.install_native_observation_candle(
                market, "005930", minutes, next_rows, source=source,
                request_as_of=requested_next, received_at=clock[0],
                identity=(1, "NX-SESSION"), request_id=next_id, now=clock[0],
                request_receipt=(next_started, next_response))
            assert accepted["ok"] is True
        assert len(published_candles) == 2
        assert key in flow._native_selected_pending
        busy[0] = False
        flow._start_pending_window_work(key)
        assert len(targets) == 1
        targets.pop(0)()
        assert len(published_candles) == 3
        assert published_candles[-1][-1]["close"] == 116
        direct = flow._historical_pools[key]["native_live_completed"]
        assert {f"20261026{hour:02d}{minute:02d}00" for minute in
                (completed_minute - minutes, completed_minute)} <= {
                    row["time"] for row in direct}
        clock[0] = now.replace(minute=forming_minute + minutes, second=6)
        completed_id = f"native-m{minutes}-completed"
        completed_rows = [_opt10080_row(
            f"20261026{hour:02d}{forming_minute:02d}00", 113)]
        completed_started = dict(started, rqname=completed_id,
                                 request_as_of=clock[0].replace(second=1).isoformat(),
                                 started_at=clock[0].replace(second=1).isoformat())
        completed_response = dict(completed_started, interval=minutes,
                                  request_id=completed_id, rows=completed_rows,
                                  received_at=clock[0].isoformat())
        accepted = MarketDataHost.install_native_observation_candle(
            market, "005930", minutes, completed_rows, source=source,
            request_as_of=clock[0].replace(second=1), received_at=clock[0],
            identity=(1, "NX-SESSION"), request_id=completed_id, now=clock[0],
            request_receipt=(completed_started, completed_response))
        assert accepted["ok"] is True
        assert signal.events[-1]["source"] == "NATIVE_SELECTED_COMPLETED_ACCEPTED"
        assert len(targets) == 1
        targets.pop(0)()
        assert len(published_candles) == 4
        assert published_candles[-1][-1]["time"] == (
            f"20261026{hour:02d}{forming_minute:02d}00")
        assert published_candles[-1][-1]["is_complete"] is True
        assert historical["candles"] == historical_before
        next_time = clock[0].replace(second=10)
        clock[0] = next_time
        latest_bar_minute = forming_minute + minutes
        next_id = f"native-m{minutes}-marking-race"
        next_rows = [_opt10080_row(
            f"20261026{hour:02d}{latest_bar_minute:02d}00", 119), completed_rows[0]]
        next_started = dict(started, rqname=next_id,
                            request_as_of=next_time.replace(second=9).isoformat(),
                            started_at=next_time.replace(second=9).isoformat())
        next_response = dict(next_started, interval=minutes, request_id=next_id,
                             rows=next_rows, received_at=next_time.isoformat())
        marked = [False]
        window.marking_display_check = SimpleNamespace(isChecked=lambda: marked[0])
        late_busy = [False]
        window.is_chart_interaction_busy = lambda: late_busy[0]
        accepted = MarketDataHost.install_native_observation_candle(
            market, "005930", minutes, next_rows, source=source,
            request_as_of=next_time.replace(second=9), received_at=next_time,
            identity=(1, "NX-SESSION"), request_id=next_id, now=next_time,
            request_receipt=(next_started, next_response))
        assert accepted["ok"] is True
        assert len(targets) == 1
        if review_case == "base":
            marked[0] = True
        elif review_case == "range_late":
            window.validation_range = (0, 0)
        elif review_case == "replay_late":
            window._replay_snapshot = object()
        else:
            late_busy[0] = True
        targets.pop(0)()
        assert len(published_candles) == 4, "NATIVE_RESULT_OVERWROTE_MARKING_VIEW"
        assert len(published_signals) == 4, "NATIVE_SIGNAL_OVERWROTE_MARKING_VIEW"
        newer_time = next_time.replace(second=12)
        clock[0] = newer_time
        newer_id = f"native-m{minutes}-marking-latest"
        newer_rows = [_opt10080_row(
            f"20261026{hour:02d}{latest_bar_minute:02d}00", 120), completed_rows[0]]
        newer_started = dict(started, rqname=newer_id,
                             request_as_of=newer_time.replace(second=11).isoformat(),
                             started_at=newer_time.replace(second=11).isoformat())
        newer_response = dict(newer_started, interval=minutes, request_id=newer_id,
                              rows=newer_rows, received_at=newer_time.isoformat())
        accepted = MarketDataHost.install_native_observation_candle(
            market, "005930", minutes, newer_rows, source=source,
            request_as_of=newer_time.replace(second=11), received_at=newer_time,
            identity=(1, "NX-SESSION"), request_id=newer_id, now=newer_time,
            request_receipt=(newer_started, newer_response))
        assert accepted["ok"] is True
        assert flow._native_selected_pending[key][1]["native_live_snapshot"]["request_id"] == newer_id
        marked[0] = False
        late_busy[0] = False
        window.validation_range = None
        window._replay_snapshot = None
        flow._start_pending_window_work(key)
        assert len(targets) == 1
        targets.pop(0)()
        assert len(published_candles) == 5
        assert published_candles[-1][-1]["close"] == 121
        assert len(published_signals) == 5


def test_nx_tr_then_tick_reaches_v5_final_candle_and_current_selector(tmp_path, monkeypatch):
    test_actual_host_native_selected_event_reaches_final_publication(
        tmp_path, monkeypatch, "NXT", 3, 8, 39, 36, "tick")


def test_krx_m5_tr_then_registered_tick_reaches_v5_current_selector(tmp_path, monkeypatch):
    test_actual_host_native_selected_event_reaches_final_publication(
        tmp_path, monkeypatch, "KRX", 5, 9, 40, 35, "tick")


@pytest.mark.parametrize("timeframe", ["D1", "W1", "MO1", "Y1"])
def test_period_marking_uses_verified_display_source_view(tmp_path, monkeypatch, timeframe):
    flow, _, prepared, failures, _, _ = _period_run(tmp_path, monkeypatch, timeframe)
    assert not failures and prepared
    window = prepared[0]["window"]
    session, pool = prepared[0]["session"], prepared[0]["pool"]
    key = id(window)
    flow._history_targets[key] = 11
    view = flow._validation_pool_for_session(session, pool, target_count=11)
    assert view["candles"][-1]["time"] != pool["candles"][-1]["time"]
    window._marking_generation = 1
    window._marking_key = ("offline-period-mark", timeframe)
    disabled, tooltips, applied = [], [], []
    window.marking_display_check = SimpleNamespace(isChecked=lambda: True,
                                                    setToolTip=tooltips.append)
    window._disable_marking = lambda **kwargs: disabled.append(kwargs)
    window.apply_marking_snapshot = lambda *args: applied.append(args) or True
    calc = view["candles"]
    request = {"settings": session.request.settings_snapshot, "generation": 1,
               "key": window._marking_key, "calculation": calc,
               "display": [calc[-1]], "times": (calc[-1]["time"], calc[-1]["time"]),
               "range": (len(calc) - 1, len(calc) - 1), "mixed": False,
               "descriptors": ()}
    with (patch.object(v5, "Thread", side_effect=lambda **kwargs: SimpleNamespace(
            start=kwargs["target"])), patch.object(v5, "_trace_v5_marking")):
        flow._start_marking(window, request)
    assert not disabled, tooltips
    assert applied, tooltips


def test_open_nx_window_requests_al_history_once_on_first_al_proof(tmp_path, monkeypatch):
    flow, _, prepared, failures, clock, _ = native._run_nxt(
        tmp_path, monkeypatch, interval=3, history_count=1000)
    assert not failures and prepared
    window = prepared[0]["window"]
    key = id(window)
    assert flow._historical_pools[key]["market_source"] == "NXT"
    assert "005930" in flow._validation_nxt_pre_codes
    clock[0] = clock[0].replace(hour=9, minute=0, second=6)
    proof = {"stock_code": "005930", "timeframe_minutes": 3,
             "request_id": "al-first", "market_source": "INTEGRATED",
             "market_data_identity": "005930_AL", "forming_candle": None,
             "completed_candles": ()}
    event = dict(source="NATIVE_SELECTED_COMPLETED_ACCEPTED", **proof)
    market = SimpleNamespace(native_selected_observation_snapshot=lambda *_: proof)
    owner = QObject()
    owner.existing_market_data_host = lambda: market
    flow.setParent(owner)
    flow._fixture_owner = owner
    flow._sor_m1_observed_host = market
    flow._v5_lease_hosts[key] = market
    calls = []
    with (patch.object(v5, "_v5_host_native_proof_is_current", return_value=True),
          patch.object(flow, "_run_validation", side_effect=lambda *a, **k: calls.append((a, k)))):
        flow._handle_native_selected_snapshot_accepted(event)
        flow._handle_native_selected_snapshot_accepted(event)
    assert len(calls) == 1
    assert calls[0][0][0] is window
    assert calls[0][0][1].force_historical_refresh is False


def test_open_nx_window_acquires_al_once_then_publishes_native_m3(tmp_path, monkeypatch):
    flow, _, prepared, failures, clock, broker_session = native._run_nxt(
        tmp_path, monkeypatch, interval=3, history_count=1000)
    assert not failures and prepared
    window = prepared[0]["window"]
    key = id(window)
    nx_pool = flow._historical_pools[key]
    nx_before = deepcopy(nx_pool["candles"])
    clock[0] = clock[0].replace(hour=9, minute=0, second=6)
    now = clock[0]
    al_candles = [dict(row, time=row["time"][:8] + "09" + row["time"][10:])
                  for row in nx_before]
    history_calls = []

    def request_al(code, name, **kwargs):
        history_calls.append((code, kwargs["interval"], kwargs["count"]))
        started = dict(ok=True, code=code, rqname="al-history-1",
                       market_data_identity="005930_AL", market_source="INTEGRATED",
                       display_only=True, connection_epoch=1,
                       login_session_id="NX-SESSION", request_as_of=now.isoformat(),
                       started_at=now.isoformat())
        kwargs["callback"](dict(started, type="minute_candles", interval=3,
                                 request_id="al-history-1", trcode="opt10080",
                                 rows=flow._historical_rows_from_candles(al_candles),
                                 rows_count=len(al_candles),
                                 received_at=now.isoformat()))
        return started

    flow._broker.request_minute_candles_display_only_integrated = request_al
    flow._cached_history = lambda *_a, **_k: None
    window._normal_base_ready = True
    window._normal_base_requested = True
    window.marking_display_check = SimpleNamespace(isChecked=lambda: False)
    window.is_chart_interaction_busy = lambda: False
    window._filter_display_descriptors = lambda _rules: []
    window.install_normal_base = lambda *_a, **_k: None
    charts, signals = [], []
    window.set_historical_candle_pool = lambda candles, **_k: charts.append(
        [dict(row) for row in candles])
    window.set_historical_display_candles = lambda *_a, **_k: None
    window.set_live_sor_monitor_signal = lambda signal, **_k: signals.append(signal)
    flow._history_targets[key] = 1
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
    requested = now.replace(second=1)
    rows = [_opt10080_row("20261026090000", 111)]
    started = dict(ok=True, code="005930", rqname="al-native-1",
                   market_data_identity="005930_AL", market_source="INTEGRATED",
                   display_only=True, connection_epoch=1,
                   login_session_id="NX-SESSION", request_as_of=requested.isoformat(),
                   started_at=requested.isoformat())
    response = dict(started, interval=3, request_id="al-native-1", rows=rows,
                    received_at=now.isoformat())
    monkeypatch.setattr(host_module, "PROJECT_ROOT", tmp_path)
    with (patch.object(v5, "Thread", side_effect=lambda **kwargs: SimpleNamespace(
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
    proof = market.native_selected_observation_snapshot(key, "005930", 3, "al-native-1")
    assert proof is not None
    assert v5._v5_host_native_proof_is_current(window.stock, proof, flow._broker, now)
    assert signal.events
    assert len(history_calls) == 1, window.errors
    assert history_calls[0][1] == 3
    assert flow._historical_pools[key]["market_source"] == "INTEGRATED"
    assert flow._native_transition_nx_pools[key] is nx_pool
    assert nx_pool["candles"] == nx_before
    assert "005930" not in flow._validation_nxt_pre_codes
    assert any(chart[-1]["time"] == "20261026090000" for chart in charts)
    assert len(signals) == 1
    assert not window.errors, window.errors
