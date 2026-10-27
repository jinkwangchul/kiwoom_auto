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
from tests.test_sor_independent_consumers_20261026 import CODE, _host
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
    assert after.get("availability_state") != "SOR_M1_SOURCE_UNAVAILABLE", after
    broker.broker_session_snapshot = lambda: SimpleNamespace(
        connected=True, connection_epoch=8, login_session_id="next")
    assert MarketDataHost._current_display_candles(host, CODE, 1) == []


def test_native_arrival_restarts_unready_m1_chart_preparation(monkeypatch):
    host, broker, clock = _host(monkeypatch)
    flow = _flow()
    session = _session()
    owner = QObject()
    owner.existing_market_data_host = lambda: host
    flow.setParent(owner)
    flow._fixture_owner = owner
    flow._broker = broker
    flow._now_factory = lambda: clock[0]
    window = SimpleNamespace(stock=session.request.stock, _normal_base_ready=False,
                             _normal_base_requested=True)
    key = id(window)
    pool = {"stock": session.request.stock, "timeframe_key": "M1",
            "timeframe_minutes": 1, "market_source": "INTEGRATED",
            "market_data_identity": f"{CODE}_AL", "candles": []}
    flow._open_windows[key] = window
    flow._validation_sessions[key] = session
    flow._historical_pools[key] = pool
    flow._validation_source_by_code[CODE] = True
    flow._v5_lease_hosts[key] = host
    host.set_v5_observation_lease(key, CODE, timeframe_minutes=1)
    flow._bind_sor_m1_observations()
    attempts = []
    monkeypatch.setattr(flow, "_use_pool_for_window",
                        lambda *args, **kwargs: attempts.append((args, kwargs)))
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
    broker.response_rows = [_opt10080_row("20261026090300", 103),
                            _opt10080_row("20261026090200", 102),
                            _opt10080_row("20261026090100", 101)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    with patch.object(host_module, "datetime", Clock):
        broker.finish()
    proof = host.native_selected_observation_snapshot(key, CODE, 1, started["rqname"])
    assert flow_module._v5_host_native_proof_is_current(
        session.request.stock, proof, broker, clock[0])
    assert key in flow._native_selected_pending
    assert len(attempts) == 1
    assert attempts[0][0][:2] == (window, session)
    assert attempts[0][0][2]["native_live_snapshot"] == proof
    assert MarketDataHost._current_display_candles(host, CODE, 1)
    assert broker.requests == [(CODE, 1, 3)]


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
    clock[0] = clock[0].replace(second=3)
    host.set_v5_observation_lease("chart", CODE, timeframe_minutes=1)
    host._refresh_v5_observation(now=clock[0])
    broker.response_rows = [_opt10080_row("20261026090300", 103),
                            _opt10080_row("20261026090200", 102)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    with patch.object(host_module, "datetime", Clock):
        broker.finish()
    source = MarketDataHost._current_display_candles(host, CODE, 1)
    assert [row["bar_time"] for row in source] == ["2026-10-26T09:02:00+09:00"]
