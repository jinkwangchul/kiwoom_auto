"""Offline direct-request/replay coverage; these receipts are not live evidence."""
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import gui_indicator_follow_signal_validation_v5_flow as v5
from tests.test_v5_pre_al_first_contract_20261101 import case as source_case
from tests.test_display_only_integrated_history import _frozen_source_plan
from routines.지표추종매매 import routine_validation_historical as historical
from tests.test_indicator_follow_signal_validation_v5_baseline import case as gui_case
from gui_indicator_follow_signal_validation_v5_window import IndicatorFollowSignalValidationV5Window
from tests.test_v5_async_marking_range_interleaving_20261031 import no_external_effects
from engines.indicator_engine import simple_ma
from engines.condition_engine import evaluate_condition


@pytest.fixture(autouse=True)
def request_clock(source_case, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return source_case.clock[0].astimezone(tz)
    monkeypatch.setattr(historical, "datetime", Clock)


def rows_for(key):
    stamps = ({"M1": ("20261030083900", "20261030084000", "20261030084100"),
               "M3": ("20261030083300", "20261030083600", "20261030083900")}
              .get(key, ("20240102000000", "20250102000000", "20261030000000")))
    return [dict(time=t, open=100., high=102., low=99., close=101., volume=10.)
            for t in stamps]


def acquire(c, key="M3", window=None):
    window = c.start(key=key, window=window)
    call = c.calls[-1]
    rows = rows_for(key)
    c.clock[0] += timedelta(seconds=1)
    period = key in {"D1", "W1", "MO1", "Y1"}
    response = dict(call["started"], ok=True,
        type="period_candles" if period else "minute_candles",
        request_id=call["started"]["rqname"],
        trcode={"D1": "opt10081", "W1": "opt10082", "MO1": "opt10083",
                "Y1": "opt10094"}.get(key, "opt10080"),
        rows=c.flow._historical_rows_from_candles(rows), rows_count=len(rows),
        received_at=c.clock[0].isoformat(), has_more=False, warning="")
    response.update({"timeframe_key": key} if period else {"interval": int(key[1:])})
    call["options"]["callback"](response)
    assert not c.failures and c.prepared, c.failures
    p = c.prepared[-1]
    return SimpleNamespace(window=window, session=p["session"], pool=p["pool"],
                           rows=rows, response=response)


def replay(c, r, view):
    with _frozen_source_plan() as root, patch("routine_signal_probe.PROJECT_ROOT", root):
        return c.flow._prepare_replay_result_from_pool(r.session, r.pool,
            history_target=3, prepared_view_pool=view, chart_range=(0, 2),
            expected_sor_m1_calculation=view["candles"])


@pytest.mark.parametrize("key", ["M1", "M3", "D1", "W1", "MO1", "Y1"])
def test_selected_direct_tr_keeps_current_in_calculation_and_range_after_clock(source_case, key):
    c = source_case
    r = acquire(c, key)
    view = c.flow._validation_pool_for_session(r.session, r.pool, target_count=3)
    frozen = c.flow._sor_m1_calculation_snapshot(r.session, r.pool)
    view = c.flow._sor_m1_calculation_view(r.session, r.pool, view, frozen)
    assert [row["time"] for row in view["candles"]] == [row["time"] for row in r.rows]
    assert view["candles"][-1]["close"] == 101.
    assert view["candles"][-1].get("is_complete") is not True
    before = deepcopy(view["candles"])
    c.clock[0] += timedelta(days=370)
    later = c.flow._validation_pool_for_session(r.session, r.pool, target_count=3)
    later = c.flow._sor_m1_calculation_view(r.session, r.pool, later,
        c.flow._sor_m1_calculation_snapshot(r.session, r.pool))
    assert later["candles"] == before
    assert later["display_candles"] == before
    closes = [row["close"] for row in later["candles"]]
    series = {"CLOSE": closes, "MA3": simple_ma(closes, 3)}
    condition = dict(target="MA", period=3, operator=">", value=100)
    assert series["MA3"] == [None, None, 101.]
    filtered = evaluate_condition(condition, series, 2, readiness_safe=True)
    assert filtered.available and filtered.passed
    rules = r.session.request.settings_snapshot.to_dict()
    rules.update(enabled=True, indicators={"moving_averages": [3]},
        buy={"delay_bar": 0, "groups": [{"conditions": [condition]}]},
        sell={"signals": {"macd_sell": {"enabled": False},
                          "profit_rate_sell": {"enabled": False}}})
    r.session = c.flow._range_replay_session(SimpleNamespace(
        _marking_current_settings=lambda: v5.ValidationSettingsSnapshot(rules)), r.session)
    result = replay(c, r, later)
    assert result.ok and result.snapshot.to_candles() == before
    assert result.snapshot.evaluated_end_index == 2
    assert [(entry.signal, entry.evaluation_index) for entry in result.snapshot.to_entries()
            if entry.signal] == [("BUY", 2)]
    assert len(c.calls) == 1


@pytest.mark.parametrize("kind", ["NATIVE_SELECTED_TICK_ACCEPTED", "SOR_AL_NATIVE_M1_PARITY_ACCEPTED"])
def test_tick_notification_has_no_v5_state_or_host_side_effect(source_case, kind, monkeypatch):
    c = source_case
    r = acquire(c)
    before = deepcopy((c.flow._request_generation, c.flow._native_selected_pending,
                       c.flow._native_selected_last, r.pool))
    # Accessing the shared host is already a forbidden tick-consumption effect.
    monkeypatch.setattr(c.flow, "parent", lambda: pytest.fail("V5 consumed a tick"))
    c.flow._handle_sor_m1_snapshot_accepted(dict(source=kind))
    if kind == "NATIVE_SELECTED_TICK_ACCEPTED":
        c.flow._handle_native_selected_snapshot_accepted(dict(source=kind))
    assert (c.flow._request_generation, c.flow._native_selected_pending,
            c.flow._native_selected_last, r.pool) == before


def test_new_authentic_selected_tr_replaces_only_its_own_generation(source_case):
    c = source_case
    old = acquire(c)
    old_generation = c.flow._request_generation[id(old.window)]
    new = acquire(c, window=old.window)
    assert new.pool["request_id"] != old.pool["request_id"]
    assert c.flow._request_generation[id(old.window)] == old_generation + 1
    with pytest.raises(ValueError):
        c.flow._validation_pool_for_session(old.session, old.pool)
    assert c.flow._validation_pool_for_session(new.session, new.pool)["candles"][-1]["close"] == 101.


@pytest.mark.parametrize("defect", ["session", "generation", "content", "receipt"])
def test_accepted_snapshot_does_not_authorize_changed_input(source_case, defect):
    c = source_case
    r = acquire(c)
    if defect == "session":
        c.broker_session.login_session_id = "OTHER"
    elif defect == "generation":
        c.flow._request_generation[id(r.window)] += 1
    elif defect == "content":
        r.pool["candles"][0]["close"] = 999.
    else:
        r.pool["direct_selected_query"]["request_id"] = "OTHER"
    with pytest.raises(ValueError):
        c.flow._validation_pool_for_session(r.session, r.pool)


@pytest.fixture
def native(source_case, monkeypatch, request):
    c = source_case
    window = None
    if getattr(request, "param", None) == "ui":
        gui = request.getfixturevalue("gui_case")
        with patch.object(v5.QTimer, "singleShot"):
            window = IndicatorFollowSignalValidationV5Window(c.stock, gui._seed())
        gui.widgets.append(window)
        from PyQt5.QtCore import QSignalBlocker
        with QSignalBlocker(window.basic_signal_interval_combo):
            window.basic_signal_interval_combo.setCurrentText("3")
    r = acquire(c, window=window)
    f, w = c.flow, r.window
    # As in the existing publication harness: isolate scheduling, not 900-bar readiness.
    monkeypatch.setattr(v5, "required_validation_history_context_bars", lambda _rules: 0)
    key = id(w)
    f._history_targets[key] = 3
    w._normal_base_ready = w._normal_base_requested = True
    w.is_chart_interaction_busy = lambda: False
    # A second native TR uses the same selected source and request generation.
    started = dict(c.calls[0]["started"], rqname="native-refresh",
                   request_as_of=c.clock[0].isoformat(), started_at=c.clock[0].isoformat())
    response = dict(r.response, **started, request_id=started["rqname"],
                    received_at=c.clock[0].isoformat())
    completed = [dict(row, is_complete=True) for row in r.rows[:-1]]
    forming = dict(r.rows[-1], is_complete=False,
                   native_bar_end="2026-10-30T08:42:00+09:00")
    proof = dict(stock_code=c.stock.code, timeframe_minutes=3,
        market_source="INTEGRATED", market_data_identity="005930_AL",
        request_id=started["rqname"], session_identity=(7, c.broker_session.login_session_id),
        request_receipt=(started, response), completed_candles=completed,
        forming_candle=forming, tr_forming_candle=deepcopy(forming), tick_revision=0)
    assert v5._v5_host_native_proof_is_current(c.stock, proof, f._broker, c.clock[0])
    observation = [deepcopy(proof)]
    market = SimpleNamespace(native_selected_observation_snapshot=lambda *_a: deepcopy(observation[0]))
    monkeypatch.setattr(f, "parent", lambda: SimpleNamespace(existing_market_data_host=lambda: market))
    monkeypatch.setattr(f, "_start_pending_window_work", lambda *_a: None)
    f._sor_m1_observed_host = market
    f._v5_lease_hosts[key] = market
    event = dict(proof, source="NATIVE_SELECTED_FORMING_ACCEPTED")
    return SimpleNamespace(c=c, r=r, f=f, w=w, key=key, proof=proof,
                           observation=observation, event=event)


def test_native_tr_queue_publish_and_replay_ignore_another_consumers_ticks(native):
    n = native
    n.f._handle_sor_m1_snapshot_accepted(n.event)
    pending = n.f._native_selected_pending[n.key]
    queued = pending[1]
    before = deepcopy(queued["native_live_snapshot"])
    n.observation[0]["tick_revision"] = 18
    n.observation[0]["forming_candle"].update(close=102., volume=50.)
    n.f._publish_pending_native_selected(n.key)
    assert n.c.prepared[-1]["pool"] is queued
    assert queued["native_live_snapshot"] == before
    view = n.f._validation_pool_for_session(n.r.session, queued, target_count=3)
    assert view["candles"][-1]["close"] == 101.
    assert view["display_candles"][-1]["close"] == 101.
    n.c.clock[0] += timedelta(minutes=15)
    # A proven publication remains usable if the host reader later disappears.
    n.f._historical_pools[n.key] = queued
    n.observation[0] = None
    later = n.f._validation_pool_for_session(n.r.session, queued, target_count=3)
    assert later["candles"] == view["candles"]
    r = SimpleNamespace(session=n.r.session, pool=queued)
    assert replay(n.c, r, later).snapshot.to_candles() == n.r.rows


@pytest.mark.parametrize("defect", ["reader-none", "expired", "ohlcv", "raw-content", "generation", "tick-candidate"])
def test_new_native_candidate_still_requires_reader_receipt_and_current_owner(native, defect):
    n = native
    if defect == "reader-none":
        n.observation[0] = None
    elif defect == "expired":
        n.c.clock[0] += timedelta(minutes=2)
    elif defect in {"ohlcv", "raw-content"}:
        value = 999. if defect == "ohlcv" else 100.5
        n.observation[0]["forming_candle"]["close"] = value
        n.event["forming_candle"]["close"] = value
    elif defect == "generation":
        n.f._request_generation[n.key] += 1
    else:
        n.observation[0]["tick_revision"] = 18
        n.observation[0]["forming_candle"].update(snapshot_source="BROKER_AL_TR_PLUS_CONTIGUOUS_TICKS",
            tick_received_at=n.c.clock[0].isoformat(), tick_receive_sequence=18, close=102.)
        n.event["forming_candle"] = deepcopy(n.observation[0]["forming_candle"])
    before = len(n.c.prepared)
    n.f._handle_sor_m1_snapshot_accepted(n.event)
    assert n.key not in n.f._native_selected_pending
    assert len(n.c.prepared) == before


@pytest.mark.parametrize("defect", ["reader-none", "cancelled", "expired"])
def test_pending_new_native_publication_rejects_lost_proof(native, defect):
    n = native
    n.f._handle_sor_m1_snapshot_accepted(n.event)
    assert n.key in n.f._native_selected_pending
    before = len(n.c.prepared)
    if defect == "reader-none":
        n.observation[0] = None
    elif defect == "cancelled":
        n.f._request_generation[n.key] += 1
    else:
        n.c.clock[0] += timedelta(minutes=2)
    n.f._publish_pending_native_selected(n.key)
    assert n.key not in n.f._native_selected_pending
    assert len(n.c.prepared) == before


def test_normal_projection_installs_frozen_current_without_independent_live_signal(
        source_case, gui_case, monkeypatch):
    c = source_case
    with patch.object(v5.QTimer, "singleShot"):
        w = IndicatorFollowSignalValidationV5Window(c.stock, gui_case._seed())
    gui_case.widgets.append(w)
    from PyQt5.QtCore import QSignalBlocker
    with QSignalBlocker(w.basic_signal_interval_combo):
        w.basic_signal_interval_combo.setCurrentText("3")
    r = acquire(c, "M3", w)
    f = c.flow
    key = id(w)
    f._history_targets[key] = 3
    w._normal_base_requested = True
    jobs, payloads = [], []
    monkeypatch.setattr(v5, "required_validation_history_context_bars", lambda _r: 0)
    monkeypatch.setattr(v5, "Thread", lambda **kw: SimpleNamespace(start=lambda: jobs.append(kw["target"])))
    monkeypatch.setattr(f, "_emit_signal_scan_completed", payloads.append)
    monkeypatch.setattr(f, "_ensure_integrated_display_sidecar", lambda *_a: None)
    from routines.지표추종매매 import routine
    monkeypatch.setattr(routine, "evaluate_current_signal_selection",
                        lambda *_a, **_kw: pytest.fail("independent V5 live BUY/SELL"))
    # Use the real native queue, normal projection worker and GUI publication.
    request = c.prepared[-1]
    f._start_normal_base_prepare(request, 3)
    c.clock[0] += timedelta(minutes=5)
    jobs.pop()()
    payload = payloads.pop()
    assert not payload.get("error"), payload
    assert "live_native_signal" not in payload and "live_sor_signal" not in payload
    f._on_signal_scan_completed(payload)
    assert w._candles == w._calculation_candles == r.rows
    from PyQt5.QtCore import QSignalBlocker
    with QSignalBlocker(w.basic_signal_interval_combo):
        w.basic_signal_interval_combo.setCurrentText("3")
    ranges = []
    monkeypatch.setattr(f, "_emit_range_replay_completed", ranges.append)
    w._set_validation_range(0, 2)
    f._run_cached_range_replay(w, 0, 2)
    assert len(jobs) == 1
    with _frozen_source_plan() as root, patch("routine_signal_probe.PROJECT_ROOT", root):
        jobs.pop()()
    assert not ranges[0]["error"], ranges[0]["error"]
    assert ranges[0]["snapshot"].to_candles() == r.rows


def test_unbound_m1_never_reads_shared_tick_projection(source_case, monkeypatch):
    c = source_case
    r = acquire(c, "M1")
    pool = dict(r.pool)
    pool.pop("_v5_tr_snapshot")
    monkeypatch.setattr(c.flow, "parent", lambda: pytest.fail("shared M1 tick projection read"))
    assert c.flow._sor_m1_calculation_snapshot(r.session, pool) is None


def test_same_source_settings_reentry_rebinds_owned_snapshot_without_new_tr(source_case, monkeypatch):
    c = source_case
    r = acquire(c)
    monkeypatch.setattr(v5, "required_validation_history_context_bars", lambda _r: 0)
    run = v5.IndicatorFollowSignalValidationRunRequest(r.session.request.settings_snapshot, 2)
    c.flow._run_validation(r.window, run)
    assert len(c.calls) == 1 and not c.failures, c.failures
    latest = c.prepared[-1]
    view = c.flow._validation_pool_for_session(latest["session"], latest["pool"], target_count=3)
    assert view["candles"] == r.rows


def test_native_completed_tr_notification_is_still_accepted(native):
    n = native
    n.c.clock[0] += timedelta(minutes=2)
    proof = deepcopy(n.proof)
    proof["forming_candle"] = proof["tr_forming_candle"] = None
    proof["completed_candles"] = [dict(row, is_complete=True) for row in n.r.rows]
    started, response = proof["request_receipt"]
    started.update(request_as_of=n.c.clock[0].isoformat(), started_at=n.c.clock[0].isoformat())
    response.update(started, received_at=n.c.clock[0].isoformat())
    n.observation[0] = proof
    n.f._handle_sor_m1_snapshot_accepted(dict(proof, source="NATIVE_SELECTED_COMPLETED_ACCEPTED"))
    pool = n.f._native_selected_pending[n.key][1]
    view = n.f._validation_pool_for_session(n.r.session, pool, target_count=3)
    assert [row["time"] for row in view["candles"]] == [row["time"] for row in n.r.rows]
    assert all(row.get("is_complete") is True for row in view["candles"])


def test_native_worker_computes_projection_without_independent_buy_sell(native, monkeypatch):
    n = native
    n.f._handle_sor_m1_snapshot_accepted(n.event)
    n.f._publish_pending_native_selected(n.key)
    request = n.c.prepared[-1]
    monkeypatch.setattr(n.w, "_filter_display_descriptors", lambda _r: (), raising=False)
    from routines.지표추종매매 import routine
    calls = []
    monkeypatch.setattr(routine, "evaluate_current_signal_selection", lambda *_a, **_kw: calls.append(1))
    jobs, payloads = [], []
    monkeypatch.setattr(v5, "Thread", lambda **kw: SimpleNamespace(start=lambda: jobs.append(kw["target"])))
    monkeypatch.setattr(n.f, "_emit_signal_scan_completed", payloads.append)
    n.f._start_normal_base_prepare(request, 3)
    jobs.pop()()
    assert not payloads[0].get("error"), payloads[0]
    assert not calls and "live_native_signal" not in payloads[0]
    assert payloads[0]["prepared_view_pool"]["candles"][-1]["close"] == 101.


def test_changed_hidden_prefix_is_rejected_before_range_projection(source_case):
    c = source_case
    r = acquire(c)
    r.pool["candles"][0]["is_complete"] = False
    with pytest.raises(ValueError):
        c.flow._range_replay_history_view(r.session, r.pool, 2)


@pytest.fixture
def published_native(native, monkeypatch):
    n = native
    jobs, payloads, installs = [], [], []
    monkeypatch.setattr(v5, "Thread", lambda **kw: SimpleNamespace(start=lambda: jobs.append(kw["target"])))
    monkeypatch.setattr(n.f, "_emit_signal_scan_completed", payloads.append)
    monkeypatch.setattr(n.f, "_ensure_integrated_display_sidecar", lambda *_a: None)
    apply = n.w.install_normal_base

    def install(*args, **kw):
        result = apply(*args, **kw)
        installs.append(deepcopy(n.w._calculation_candles))
        return result

    monkeypatch.setattr(n.w, "install_normal_base", install)
    n.f._handle_sor_m1_snapshot_accepted(n.event)
    n.f._publish_pending_native_selected(n.key)

    def prepare(request):
        n.f._start_normal_base_prepare(request, 3)
        jobs.pop()()
        payload = payloads.pop()
        assert not payload.get("error"), payload
        return payload

    n.f._on_signal_scan_completed(prepare(n.c.prepared[-1]))
    assert len(installs) == 1
    assert n.key not in n.f._native_selected_pending
    n.pool = n.f._historical_pools[n.key]
    n.installs, n.prepare = installs, prepare
    return n


@pytest.mark.parametrize("native", ["ui"], indirect=True)
@pytest.mark.parametrize("host_state", ["reader-none", "elapsed"])
def test_audit_published_native_redisplays_without_new_adoption(published_native, host_state):
    n = published_native
    if host_state == "reader-none":
        n.observation[0] = None
    else:
        n.c.clock[0] += timedelta(minutes=15)
    n.f._use_pool_for_window(n.w, n.r.session, n.pool, evaluation_count=3,
        _normal_view=n.f._normal_base_history_view(n.r.session, n.pool, 3)[0])
    request = n.c.prepared[-1]
    assert request.get("live_native_request_id") is None
    n.f._on_signal_scan_completed(n.prepare(request))
    assert len(n.installs) == 2 and n.installs[1] == n.installs[0]


@pytest.mark.parametrize("base", ["published", "pending"])
def test_audit_native_refresh_cannot_launder_old_history(native, base):
    n = native
    if base == "pending":
        n.f._handle_sor_m1_snapshot_accepted(n.event)
        previous = n.f._native_selected_pending[n.key]
        rows = previous[1]["native_live_completed"]
        previous[1]["native_live_completed"] = (dict(rows[0], close=100.5), *rows[1:])
    else:
        previous = None
        n.r.pool["candles"][0]["close"] = 100.5
    proof = deepcopy(n.proof)
    proof["request_id"] = "next-native-tr"
    proof["completed_candles"] = proof["completed_candles"][1:]
    started, response = proof["request_receipt"]
    started["rqname"] = proof["request_id"]
    response.update(rqname=proof["request_id"], request_id=proof["request_id"],
        rows=n.f._historical_rows_from_candles(n.r.rows[1:]), rows_count=2)
    n.observation[0] = proof
    n.f._handle_sor_m1_snapshot_accepted(dict(proof, source="NATIVE_SELECTED_FORMING_ACCEPTED"))
    assert n.f._native_selected_pending.get(n.key) is previous


@pytest.mark.parametrize("native", ["ui"], indirect=True)
@pytest.mark.parametrize("defect", ["login", "epoch", "disconnect", "pool-content", "view-content",
                                      "generation", "session", "pool", "timeframe"])
def test_audit_normal_publication_rechecks_frozen_binding(published_native, defect):
    n = published_native
    n.f._use_pool_for_window(n.w, n.r.session, n.pool, evaluation_count=3,
        _normal_view=n.f._normal_base_history_view(n.r.session, n.pool, 3)[0])
    request = n.c.prepared[-1]
    # Isolate the final publication gate from the separately tested new-adoption gate.
    request["live_native_request_id"] = None
    payload = n.prepare(request)
    before = deepcopy(n.w._calculation_candles)
    if defect == "login":
        n.c.broker_session.login_session_id = "OTHER"
    elif defect == "epoch":
        n.c.broker_session.connection_epoch += 1
    elif defect == "disconnect":
        n.c.broker_session.connected = False
    elif defect == "pool-content":
        n.pool["candles"][0]["close"] = 100.5
    elif defect == "view-content":
        payload["prepared_view_pool"]["candles"][-1]["close"] = 100.5
    elif defect == "generation":
        n.f._request_generation[n.key] += 1
    elif defect == "session":
        n.f._validation_sessions[n.key] = v5.ValidationSession(n.r.session.request,
            operation_active_reader=lambda: False)
    elif defect == "pool":
        n.f._historical_pools[n.key] = dict(n.pool)
    else:
        from PyQt5.QtCore import QSignalBlocker
        with QSignalBlocker(n.w.basic_signal_interval_combo):
            n.w.basic_signal_interval_combo.setCurrentText("5")
    n.f._on_signal_scan_completed(payload)
    assert len(n.installs) == 1
    assert n.w._calculation_candles == before


def _krx_period_callback(c, monkeypatch, key, defect=None):
    from stock_market_source_plan import _build
    monkeypatch.setattr(c.flow, "_verified_nxt_validation_stock", lambda *_a: False)
    plan = _build(c.stock.code, c.stock.name, c.clock[0].date(), c.clock[0].isoformat(), False)
    monkeypatch.setattr(v5, "read_stock_market_source_plan", lambda *_a: deepcopy(plan))
    calls = []

    def request(code, name, **options):
        # Actual KRX API FULL start shape has no broker timestamp/session fields.
        started = dict(ok=True, code=code, market_data_identity=code, market_source="KRX",
            market_session_scope="FULL", timeframe_key=key, rqname="krx-period", screen_no="7001")
        if defect == "started-stock":
            started["code"] = "000660"
        calls.append((started, options))
        return started

    c.flow._broker.request_period_candles_read_only = request
    if defect == "missing-capture":
        monkeypatch.setattr(c.flow, "_remember_verified_al_period_response", lambda *_a: None)
    c.start(key=key)
    started, options = calls[0]
    c.clock[0] += timedelta(seconds=1)
    response = dict(started, type="period_candles", request_id=started["rqname"],
        code=c.stock.code,
        trcode={"D1": "opt10081", "W1": "opt10082", "MO1": "opt10083", "Y1": "opt10094"}[key],
        rows=c.flow._historical_rows_from_candles(rows_for(key)), rows_count=3,
        has_more=False, warning="")
    if defect == "login":
        c.broker_session.login_session_id = "OTHER"
    elif defect == "epoch":
        c.broker_session.connection_epoch += 1
    elif defect == "source":
        response["market_data_identity"] += "_AL"
        response["market_source"] = "INTEGRATED"
    elif defect == "request":
        response["request_id"] = "OTHER"
    elif defect == "late":
        c.clock[0] += timedelta(minutes=1)
    options["callback"](response)


@pytest.mark.parametrize("key", ["D1", "W1", "MO1", "Y1"])
def test_audit_krx_full_period_callback_establishes_own_freeze(source_case, monkeypatch, key):
    c = source_case
    _krx_period_callback(c, monkeypatch, key)
    assert not c.failures and c.prepared, c.failures
    p = c.prepared[-1]
    assert p["pool"].get("_v5_tr_snapshot") is not None
    c.clock[0] += timedelta(days=370)
    view = c.flow._validation_pool_for_session(p["session"], p["pool"], target_count=3)
    assert view["market_source"] == "KRX" and view["market_data_identity"] == c.stock.code
    assert view["candles"] == rows_for(key)
    result = replay(c, SimpleNamespace(session=p["session"], pool=p["pool"]), view)
    assert result.ok and result.snapshot.to_candles() == rows_for(key)


@pytest.mark.parametrize("defect", ["login", "epoch", "source", "request", "late", "missing-capture", "started-stock"])
def test_audit_krx_period_rejects_unauthenticated_new_callback(source_case, monkeypatch, defect):
    c = source_case
    _krx_period_callback(c, monkeypatch, "W1", defect)
    assert not c.prepared
    assert not c.flow.__dict__.get("_v5_tr_snapshots")


def test_krx_regular_freeze_uses_existing_proof_then_rejects_full_scope(source_case, monkeypatch):
    c = source_case
    freeze = c.flow._freeze_selected_direct_tr
    captured = []
    monkeypatch.setattr(c.flow, "_freeze_selected_direct_tr",
                        lambda *args: captured.append(args))
    _krx_period_callback(c, monkeypatch, "W1")
    session, pool, _ = captured[-1]
    # An offline REGULAR callback exercises the existing receipt collector, not
    # a relabelled FULL receipt. P2's selected source remains FULL throughout.
    started_at = session.source_selection.selected_at
    started = dict(ok=True, code=c.stock.code, market_data_identity=c.stock.code,
        market_source="KRX", market_session_scope="REGULAR", timeframe_key="W1",
        rqname="offline-krx-regular")
    response = dict(started, type="period_candles", request_id=started["rqname"],
        trcode="opt10102", rows=c.flow._historical_rows_from_candles(rows_for("W1")),
        rows_count=3)
    c.flow._remember_verified_al_period_response(c.stock.code, "W1", response, started,
        started_at, c.clock[0], c.broker_session, c.broker_session)
    snapshot = v5.ValidationHistoricalSnapshot(stock=c.stock, timeframe_minutes=1,
        timeframe_key="W1", requested_count=3, request_id=started["rqname"],
        rows=response["rows"], market_data_identity=c.stock.code, market_source="KRX",
        request_as_of=started_at.isoformat())
    candles, _, query = c.flow._direct_selected_history_parts(snapshot)
    assert query is not None and query["market_session_scope"] == "REGULAR"
    assert c.flow._direct_al_period_query_valid(query, c.stock, "W1", 1, c.stock.code, candles)
    assert not c.flow._direct_krx_period_query_valid(query, c.stock, "W1", 1, c.stock.code, candles)
    pool = dict(pool, request_id=snapshot.request_id, candles=candles, direct_selected_query=query)
    assert session.source_selection.market_session_scope == "FULL"
    assert c.flow._period_session_scope(v5.ValidationSettingsSnapshot(
        {"validation_market_scope": {"regular_market_only": True}}), "W1") == "FULL"
    damaged = deepcopy(pool)
    damaged["candles"][0]["close"] = 100.5
    with pytest.raises(ValueError, match="^V5_DIRECT_TR_RECEIPT_INVALID$"):
        freeze(session, damaged, snapshot)
    with pytest.raises(ValueError, match="^PERIOD_RECEIPT_SCOPE_MISMATCH$"):
        freeze(session, pool, snapshot)
    assert not c.flow.__dict__.get("_v5_tr_snapshots")


@pytest.mark.parametrize("special,defect", [(False, None), (True, None),
                                            (False, "receipt"), (False, "base")])
def test_audit_m5_extension_requires_new_whole_response_binding(source_case, monkeypatch, special, defect):
    c = source_case
    # Bound this receipt/extension fixture to five rows; production's 5000 stays unchanged.
    monkeypatch.setattr(c.flow, "_historical_count", 5)
    c.clock[0] = datetime.fromisoformat("2026-10-30T11:44:00+09:00")
    monkeypatch.setattr(v5, "required_validation_history_context_bars", lambda _r: 0)
    request = c.flow._broker.request_minute_candles_display_only_integrated

    def dispatch(*args, **kw):
        started = request(*args, **kw)
        started["screen_no"] = "7001"
        c.calls[-1]["started"]["screen_no"] = "7001"
        return started

    c.flow._broker.request_minute_candles_display_only_integrated = dispatch
    # Offline native response rows; the 15:35 row is display-only throughout.
    stamps = ["20261030" + minute for minute in
              ("111000", "111500", "112000", "112500", "113000", "113500", "114000")]
    rows = [dict(time=t, open=100., high=102., low=99., close=101., volume=10.) for t in stamps]
    initial = rows[-5:]
    if special:
        closing = dict(rows[0], time="20261029153500")
        initial = [closing, *rows[-4:]]
        rows = [closing, *rows[1:]]
    window = c.start(key="M5")

    def respond(index, values):
        call = c.calls[index]
        started = call["started"]
        c.clock[0] += timedelta(seconds=1)
        response = dict(started, type="minute_candles", interval=5, trcode="opt10080",
            request_id=started["rqname"], rows=c.flow._historical_rows_from_candles(values),
            rows_count=len(values), received_at=c.clock[0].isoformat(), has_more=True,
            warning="additional pages available", native_minute_callback=dict(
                screen_no="7001", rqname=started["rqname"], trcode="opt10080", prev_next="2",
                connection_epoch=started["connection_epoch"], login_session_id=started["login_session_id"]))
        if index == 1 and defect == "receipt":
            response.pop("native_minute_callback")
        call["options"]["callback"](response)

    respond(0, initial)
    assert not c.failures and c.prepared, c.failures
    p = c.prepared[-1]
    old_pool = p["pool"]
    before = deepcopy(old_pool["candles"])
    calculated = c.flow._normal_base_native_m5_calculation_rows(p["session"], old_pool, before)
    assert [row["time"] for row in calculated] == [row["time"] for row in before
                                                  if not row["time"].endswith("153500")]
    c.flow._history_targets[id(window)] = 3
    monkeypatch.setattr(v5, "required_validation_history_context_bars", lambda _r: 3)
    ready = []
    assert c.flow._ensure_normal_base_history(window, p["session"], old_pool, 3,
        on_ready=lambda *args: ready.append(args))
    assert len(c.calls) == 2
    old_tokens = set(c.flow._v5_tr_snapshots)
    if defect == "base":
        old_pool["candles"][0]["close"] = 100.5
    respond(1, rows[-c.calls[1]["options"]["count"]:])
    if defect is not None:
        assert not ready and set(c.flow._v5_tr_snapshots) == old_tokens
        assert c.flow._historical_pools[id(window)] is old_pool
        return
    assert ready and not c.failures, c.failures
    pool, view, deficit = ready[-1]
    assert deficit == 0
    assert pool["request_id"] != old_pool["request_id"]
    assert pool["_v5_tr_snapshot"] != old_pool["_v5_tr_snapshot"]
    assert old_pool["candles"] == before
    assert len(view["candles"]) == 6
    assert all(not row["time"].endswith("153500") for row in view["candles"])
    selected = c.flow._validation_pool_for_session(p["session"], pool, target_count=10)
    assert any(row.get("display_only_special") for row in selected["display_candles"]) is special
