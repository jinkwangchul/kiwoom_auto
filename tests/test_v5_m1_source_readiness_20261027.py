"""The accepted selected M1 TR must feed the session-bound SOR source."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from PyQt5.QtCore import QObject
import pytest

import gui_indicator_follow_signal_validation_v5_flow as flow_module
import gui_market_data_host as host_module
from candle_timeframe_aggregation import SEOUL_TIMEZONE
from gui_market_data_host import MarketDataHost
from tests.test_display_only_integrated_history import _opt10080_row
from tests.test_sor_independent_consumers_20261026 import CODE, _host, _tick
from tests.test_indicator_follow_signal_validation_v5_baseline import candles as history_candles
from tests.test_indicator_follow_signal_validation_v5_integrated_history_scroll import (
    _flow, _session,
)


def test_selected_m1_native_reply_unblocks_empty_session_source(monkeypatch):
    host, broker, clock = _host(monkeypatch)
    assert host.set_v5_observation_lease("chart", CODE, timeframe_minutes=1)["ok"]
    assert MarketDataHost._current_display_candles(host, CODE, 1) == []
    before = host.project_broker_sor_m1_calculation(
        stock_code=CODE, rules={"bar": {"bar_minutes": 1}},
        projection_request={"projection": "COMPLETED_TIMEFRAME"}, as_of=clock[0])
    assert before["availability_state"] == "SOR_M1_SOURCE_UNAVAILABLE"

    host._refresh_v5_observation(now=clock[0])
    assert broker.requests == [(CODE, 1, 3)]
    broker.response_rows = [
        _opt10080_row("20261026090300", 103),
        _opt10080_row("20261026090200", 102),
        _opt10080_row("20261026090100", 101),
    ]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    with patch.object(host_module, "datetime", Clock):
        broker.finish()
    assert broker.requests == [(CODE, 1, 3)]
    source = MarketDataHost._current_display_candles(host, CODE, 1)
    assert len(source) == 3
    assert all(row["market_source"] == "INTEGRATED" for row in source)
    after = host.project_broker_sor_m1_calculation(
        stock_code=CODE, rules={"bar": {"bar_minutes": 1}},
        projection_request={"projection": "COMPLETED_TIMEFRAME"}, as_of=clock[0])
    assert after["available"] is True, after
    assert [row["bar_time"] for row in after["candles"]] == [
        "2026-10-26T09:01:00+09:00", "2026-10-26T09:02:00+09:00",
        "2026-10-26T09:03:00+09:00"]
    broker.broker_session_snapshot = lambda: SimpleNamespace(
        connected=True, connection_epoch=8, login_session_id="next")
    assert MarketDataHost._current_display_candles(host, CODE, 1) == []


def _cold_chart_case(monkeypatch, tmp_path):
    host, broker, clock = _host(monkeypatch)
    flow = _flow()
    session = _session()
    owner = QObject()
    owner.existing_market_data_host = lambda: host
    flow.setParent(owner)
    flow._fixture_owner = owner
    flow._broker = broker
    flow._now_factory = lambda: clock[0]
    monkeypatch.setattr(flow_module, "_SOR_AL_M1_DIAGNOSTIC_PATH", tmp_path / "m1.jsonl")
    monkeypatch.setattr(flow_module, "_V5_MARKING_DIAGNOSTIC_PATH", tmp_path / "marking.jsonl")
    chart, displayed, failures = [], [], []
    window = SimpleNamespace(stock=session.request.stock, _normal_base_ready=False,
                             _normal_base_requested=True,
                             marking_display_check=SimpleNamespace(isChecked=lambda: False),
                             is_chart_interaction_busy=lambda: False,
                             _filter_display_descriptors=lambda _rules: [],
                             set_historical_candle_pool=lambda rows, **_kw:
                                 chart.append(list(rows)),
                             set_historical_display_candles=lambda rows, **_kw:
                                 displayed.append(list(rows)),
                             show_validation_error=failures.append)
    window.install_normal_base = lambda *_args, **_kw: setattr(
        window, "_normal_base_ready", True)
    key = id(window)
    historical = history_candles(902) + [
        {"time": f"20261026090{minute}00", "open": 100 + minute,
         "high": 102 + minute, "low": 98 + minute,
         "close": 101 + minute, "volume": 10}
        for minute in range(4)]
    flow._validation_source_by_code[CODE] = True
    pool = flow._pool_from_candles(
        session, historical, requested_count=len(historical),
        request_id="selected-al-m1", market_data_identity=f"{CODE}_AL",
        market_source="INTEGRATED")
    flow._open_windows[key] = window
    flow._validation_sessions[key] = session
    flow._historical_pools[key] = pool
    flow._history_targets[key] = 3
    flow._request_generation[key] = 0
    flow._v5_lease_hosts[key] = host
    host.set_v5_observation_lease(key, CODE, timeframe_minutes=1)
    flow._bind_sor_m1_observations()
    monkeypatch.setattr(flow_module, "read_stock_market_source_plan", lambda *_: {
        **host_module.read_stock_market_source_plan(None, CODE, None),
        "stock_name": "Samsung"})
    original_request = broker.request_minute_candles_display_only_integrated

    def request(*args, **kwargs):
        started = original_request(*args, **kwargs)
        started["started_at"] = clock[0].isoformat()
        broker.pending[-1][1]["started_at"] = started["started_at"]
        return started

    broker.request_minute_candles_display_only_integrated = request
    host._refresh_v5_observation(now=clock[0])
    _, started, _ = broker.pending[0]
    broker.response_rows = [_opt10080_row("20261026090400", 104),
                            _opt10080_row("20261026090300", 103),
                            _opt10080_row("20261026090200", 102),
                            ]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    def finish():
        with patch.object(host_module, "datetime", Clock):
            broker.finish()
        proof = host.native_selected_observation_snapshot(key, CODE, 1, started["rqname"])
        assert flow_module._v5_host_native_proof_is_current(
            session.request.stock, proof, broker, clock[0])
        return proof

    return flow, session, window, pool, host, broker, clock, finish, chart, displayed, failures


@pytest.mark.parametrize("blocked", ["not_requested", "replay", "range", "marked",
                                     "busy", "browse", "publication"])
def test_cold_m1_continuation_keeps_normal_mode_guards(monkeypatch, tmp_path, blocked):
    flow, _session, window, _pool, _host, _broker, _clock, finish, *_ = (
        _cold_chart_case(monkeypatch, tmp_path))
    key = id(window)
    if blocked == "not_requested":
        window._normal_base_requested = False
    elif blocked == "replay":
        window._replay_snapshot = object()
    elif blocked == "range":
        window.validation_range = (0, 1)
    elif blocked == "marked":
        window.marking_display_check.isChecked = lambda: True
    elif blocked == "busy":
        window.is_chart_interaction_busy = lambda: True
    elif blocked == "browse":
        flow._history_browse_active[key] = object()
    else:
        window._marking_pool_publication_pending = True
    attempts = []
    monkeypatch.setattr(flow, "_use_pool_for_window",
                        lambda *args, **kwargs: attempts.append((args, kwargs)))
    finish()
    assert attempts == []


@pytest.mark.parametrize("stale_generation", [False, True])
def test_cold_m1_pending_resumes_once_after_mode_unlock(monkeypatch, tmp_path,
                                                        stale_generation):
    flow, _session, window, _pool, _host, _broker, _clock, finish, *_ = (
        _cold_chart_case(monkeypatch, tmp_path))
    key = id(window)
    window.is_chart_interaction_busy = lambda: True
    attempts = []
    monkeypatch.setattr(flow, "_use_pool_for_window",
                        lambda *args, **kwargs: attempts.append((args, kwargs)))
    finish()
    assert attempts == [] and key in flow._native_selected_pending
    window.is_chart_interaction_busy = lambda: False
    if stale_generation:
        flow._request_generation[key] += 1
    flow._start_pending_window_work(key)
    flow._start_pending_window_work(key)
    assert len(attempts) == (0 if stale_generation else 1)


def test_cold_m1_arrival_waits_for_active_prepare_then_publishes_retained_history(
        monkeypatch, tmp_path):
    flow, session, window, pool, host, broker, clock, finish, chart, displayed, failures = (
        _cold_chart_case(monkeypatch, tmp_path))
    key = id(window)
    targets = []
    payloads = []
    flow.signal_scan_completed.connect(payloads.append)

    class QueuedThread:
        def __init__(self, *, target, **_kw):
            self.target = target

        def start(self):
            targets.append(self.target)

    monkeypatch.setattr(flow_module, "Thread", QueuedThread)
    _, deficit = flow._normal_base_history_view(session, pool, 3)
    assert deficit == 0
    flow._use_pool_for_window(window, session, pool, evaluation_count=100)
    assert len(targets) == 1 and key in flow._prepare_active
    first_token = flow._prepare_active[key]
    proof = finish()
    assert proof["forming_candle"]["time"] == "20261026090400"
    assert MarketDataHost._current_display_candles(host, CODE, 1)
    assert flow._prepare_active[key] is first_token
    assert key not in flow._prepare_pending
    _tick(host, broker, clock, sequence=1)
    assert flow._prepare_active[key] is first_token
    assert key not in flow._prepare_pending
    _tick(host, broker, clock, sequence=2)
    assert flow._prepare_active[key] is first_token
    assert key not in flow._prepare_pending
    clock[0] += flow_module.timedelta(seconds=10)
    host._refresh_v5_observation(now=clock[0])

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    with patch.object(host_module, "datetime", Clock):
        broker.finish()
    assert flow._prepare_active[key] is first_token
    assert key not in flow._prepare_pending
    assert len(targets) == 1
    targets.pop(0)()
    assert payloads[0].get("error") == "SOR_M1_BLOCKED:SOR_M1_SOURCE_UNAVAILABLE"
    assert len(targets) == 1  # The failed old snapshot starts one current-source prepare.
    assert failures == []
    assert flow._historical_pools[key]["candles"] is pool["candles"]
    targets.pop(0)()
    assert window._normal_base_ready is True
    assert failures == []
    assert chart and displayed
    assert chart[-1][-1]["time"] == "20261026090400"
    assert len(chart[-1]) >= 903
    assert any(row["time"] < "20261026" for row in chart[-1])
    assert len(displayed[-1]) == 3
    assert broker.requests == [(CODE, 1, 3)] * 2


@pytest.mark.parametrize("rejection", ["wrong_session", "wrong_source", "late", "m3"])
def test_rejected_or_other_frame_reply_never_supplies_m1(monkeypatch, rejection):
    host, broker, clock = _host(monkeypatch)
    frame = 3 if rejection == "m3" else 1
    host.set_v5_observation_lease("chart", CODE, timeframe_minutes=frame)
    host._refresh_v5_observation(now=clock[0])
    callback, started, interval = broker.pending.pop(0)
    if rejection == "late":
        host.set_v5_observation_lease("chart", CODE, timeframe_minutes=3)
    response = {**started, "interval": interval, "request_id": started["rqname"],
                "rows": [_opt10080_row("20261026090300", 103)],
                "received_at": clock[0].isoformat()}
    if rejection == "wrong_session":
        response["login_session_id"] = "other"
    if rejection == "wrong_source":
        response["market_source"] = "NXT"

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    with patch.object(host_module, "datetime", Clock):
        callback(response)
    assert MarketDataHost._current_display_candles(host, CODE, 1) == []


def test_rollover_reply_does_not_promote_request_time_forming_bar(monkeypatch):
    host, broker, clock = _host(monkeypatch)
    clock[0] = clock[0].replace(second=10)  # Broker request_as_of is four seconds earlier.
    host.set_v5_observation_lease("chart", CODE, timeframe_minutes=1)
    host._refresh_v5_observation(now=clock[0])
    broker.response_rows = [_opt10080_row("20261026090400", 104),
                            _opt10080_row("20261026090300", 103)]
    clock[0] = clock[0].replace(minute=5, second=1)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    with patch.object(host_module, "datetime", Clock):
        broker.finish()
    source = MarketDataHost._current_display_candles(host, CODE, 1)
    assert [row["bar_time"] for row in source] == ["2026-10-26T09:03:00+09:00"]
    proof = host.native_selected_observation_snapshot("chart", CODE, 1, "native-1")
    assert proof["forming_candle"] is None
    assert [row["time"] for row in proof["completed_candles"]] == ["20261026090300"]


def test_late_m1_receipt_cannot_supply_session_source(monkeypatch):
    host, broker, clock = _host(monkeypatch)
    host.set_v5_observation_lease("chart", CODE, timeframe_minutes=1)
    host._refresh_v5_observation(now=clock[0])
    broker.response_rows = [_opt10080_row("20261026090400", 104),
                            _opt10080_row("20261026090300", 103)]
    clock[0] += flow_module.timedelta(seconds=65)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    with patch.object(host_module, "datetime", Clock):
        broker.finish()
    assert MarketDataHost._current_display_candles(host, CODE, 1) == []
    assert host._native_observation_receipt_by_stock.get((CODE, 1)) is None


def test_completed_only_m3_keeps_existing_anchor_proof_gate(monkeypatch):
    host, broker, clock = _host(monkeypatch)
    host.set_v5_observation_lease("chart", CODE, timeframe_minutes=3)
    host._refresh_v5_observation(now=clock[0])
    broker.response_rows = [_opt10080_row("20261026090000", 100)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    with patch.object(host_module, "datetime", Clock):
        broker.finish()
    assert host._native_observation_receipt_by_stock[(CODE, 3)]["tick_anchor_sequence"] == 0
    assert host.native_selected_observation_snapshot("chart", CODE, 3, "native-1") is None
