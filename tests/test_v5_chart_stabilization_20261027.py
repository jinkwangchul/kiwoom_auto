"""Focused offline regressions for native M3 provenance and range gestures."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from PyQt5.QtCore import QEvent, QObject, QPoint, QPointF, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from gui_indicator_follow_signal_validation_v5_window import (
    IndicatorFollowSignalValidationChartCanvas as V5Canvas,
)
from gui_indicator_follow_signal_validation_v5_flow import (
    _V5SelectedSourceRequester, ValidationHistoricalSnapshot,
    ValidationRequest, ValidationSession, ValidationSettingsSnapshot,
    ValidationStockRef,
)
from tests.test_v5_direct_forming_tail_20261026 import AS_OF, _flow, _session


@pytest.mark.parametrize("key", [
    "M1", "M3", "M5", "M10", "M15", "M30", "M60", "M120", "M240",
    "D1", "W1", "MO1", "Y1",
])
def test_each_selected_timeframe_requests_its_native_integrated_tr(key):
    calls = []

    class Broker:
        def __getattr__(self, name):
            def request(code, stock_name, **kwargs):
                calls.append((name, code, stock_name, kwargs))
                return {"ok": True}
            return request

    stock = ValidationStockRef("005930", "Samsung")
    requester = _V5SelectedSourceRequester(
        Broker(), stock, key, True, now_factory=lambda: AS_OF,
    )
    callback = lambda _result: pytest.fail("native request rejected")
    if key.startswith("M") and key[1:].isdigit():
        requester.request_minute_candles_read_only(
            stock.code, stock.name, interval=int(key[1:]),
            count=7, callback=callback,
        )
        assert calls[0][0] == "request_minute_candles_display_only_integrated"
        assert calls[0][3]["interval"] == int(key[1:])
    else:
        requester.request_period_candles_read_only(
            stock.code, stock.name, timeframe_key=key,
            count=7, callback=callback,
        )
        assert calls[0][0] == "request_period_candles_read_only_integrated"
        assert calls[0][3]["timeframe_key"] == key
    assert len(calls) == 1 and calls[0][1:3] == (stock.code, stock.name)


@pytest.mark.parametrize("key,minutes,stamps,bad_stamp", [
    ("M3", 3, ("20261023154200", "20261026161800", "20261026162100"),
     "20261023154000"),
    ("M15", 15, ("20261023154500", "20261026160000", "20261026161500"),
     "20261023155000"),
])
def test_native_after_history_uses_its_tr_without_missing_anchor_trade(
        key, minutes, stamps, bad_stamp):
    flow = _flow()
    stock = ValidationStockRef("005930", "Samsung")
    settings = ValidationSettingsSnapshot({
        "bar": {"bar_minutes": minutes}, "validation_timeframe": {"key": key},
    })
    session = ValidationSession(ValidationRequest(stock, settings, minutes),
                                operation_active_reader=lambda: False)
    flow._validation_source_by_code["005930"] = True
    rows = [
        {"time": stamp, "open": 100, "high": 102, "low": 99,
         "close": 101, "volume": 10}
        for stamp in stamps
    ]
    snapshot = ValidationHistoricalSnapshot(
        stock=stock, timeframe_minutes=minutes, timeframe_key=key,
        requested_count=3, request_id=f"native-{key}",
        rows=flow._historical_rows_from_candles(rows),
        market_data_identity="005930_AL", market_source="INTEGRATED",
        request_as_of=AS_OF.isoformat(),
    )
    completed, forming, query = flow._direct_selected_history_parts(snapshot)
    pool = flow._pool_from_candles(
        session, completed, requested_count=3, request_id=query["request_id"],
        market_data_identity="005930_AL", market_source="INTEGRATED",
        direct_selected_query=query, selected_direct_forming_snapshot=forming,
    )
    flow._now_factory = lambda: AS_OF
    assert not (flow._sor_m3_after_anchor_verified(pool, AS_OF)
                if key == "M3" else flow._sor_m15_after_anchor_verified(pool, AS_OF))
    view = flow._validation_pool_for_session(session, pool, target_count=3)
    result = flow._sor_m1_calculation_view(
        session, pool, view, flow._sor_m1_calculation_snapshot(session, pool),
    )
    assert result["candles"] == rows

    bad = dict(pool, candles=[dict(completed[0], time=bad_stamp), *completed[1:]])
    bad_view = flow._validation_pool_for_session(session, bad, target_count=3)
    with pytest.raises(ValueError, match="SOR_DIRECT_HISTORY_PROVENANCE_INVALID"):
        flow._sor_m1_calculation_view(
            session, bad, bad_view, flow._sor_m1_calculation_snapshot(session, bad),
        )
    unproven = dict(pool, direct_selected_query=None)
    unproven_view = flow._validation_pool_for_session(session, unproven, target_count=3)
    with pytest.raises(ValueError, match="SOR_DIRECT_HISTORY_PROVENANCE_INVALID"):
        flow._sor_m1_calculation_view(
            session, unproven, unproven_view,
            flow._sor_m1_calculation_snapshot(session, unproven),
        )


def test_m1_snapshot_event_cannot_republish_a_selected_m3_window(monkeypatch):
    flow, session = _flow(), _session()
    identity = (7, "same-login")
    received = AS_OF.isoformat()
    candle = {"bar_time": "2026-10-26T16:21:00+09:00",
              "snapshot_received_at": received,
              "snapshot_source": "BROKER_AL_TR_FORMING_M1"}
    market = SimpleNamespace(
        _sor_m1_required_by_stock={"005930": True},
        _sor_forming_m1_by_stock={"005930": {
            "session_identity": identity, "candle": candle,
        }},
        _current_display_session_identity=lambda: identity,
    )
    owner = QObject()
    owner.existing_market_data_host = lambda: market
    flow.setParent(owner)
    flow._sor_m1_observed_host = market
    flow._validation_source_by_code["005930"] = True
    window = SimpleNamespace(marking_display_check=SimpleNamespace(
        isChecked=lambda: False), is_chart_interaction_busy=lambda: False,
        _normal_base_ready=True, _normal_base_requested=True,
        _replay_snapshot=None, validation_range=None)
    key = id(window)
    flow._open_windows[key] = window
    flow._validation_sessions[key] = session
    flow._historical_pools[key] = {
        "stock": session.request.stock, "timeframe_key": "M3",
        "timeframe_minutes": 3, "market_source": "INTEGRATED",
        "market_data_identity": "005930_AL",
    }
    monkeypatch.setattr(flow, "_normal_base_history_view", lambda *_args, **_kwargs:
                        pytest.fail("M1 event reached M3 publication"))
    flow._handle_sor_m1_snapshot_accepted({
        "source": "SOR_AL_M1_SNAPSHOT_ACCEPTED", "stock_code": "005930",
        "market_data_identity": "005930_AL", "connection_epoch": identity[0],
        "login_session_id": identity[1], "forming_minute": candle["bar_time"],
        "snapshot_received_at": received,
    })


@pytest.mark.parametrize("release_before_drag", [False, True])
def test_double_click_drag_selects_period_without_panning(release_before_drag):
    app = QApplication.instance() or QApplication([])
    first = datetime(2026, 10, 26, 9)
    candles = [
        {"time": (first + timedelta(minutes=i)).strftime("%Y%m%d%H%M%S"),
         "open": 100, "high": 101, "low": 99, "close": 100, "volume": 10}
        for i in range(20)
    ]
    canvas = V5Canvas(candles, [], timeframe_key="M1")
    canvas.resize(800, 360)
    canvas.show()
    app.processEvents()
    start = QPoint(round(canvas._x_for_index(3)), 120)
    end = QPoint(round(canvas._x_for_index(9)), 120)
    selected, panned = [], []
    canvas.validation_range_drag_completed.connect(lambda a, b: selected.append((a, b)))
    canvas.pan_requested.connect(lambda dx, dy: panned.append((dx, dy)))

    QTest.mouseDClick(canvas, Qt.LeftButton, pos=start)
    if release_before_drag:
        QTest.mouseRelease(canvas, Qt.LeftButton, pos=start)
        assert canvas.validation_range_drag_start_index == 3
        hover = QMouseEvent(QEvent.MouseMove, QPointF(end), Qt.NoButton,
                            Qt.NoButton, Qt.NoModifier)
        QApplication.sendEvent(canvas, hover)
        assert canvas._validation_range_drag_current_index == 3
        QTest.mousePress(canvas, Qt.LeftButton, pos=start)
    move = QMouseEvent(QEvent.MouseMove, QPointF(end), Qt.NoButton,
                       Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(canvas, move)
    QTest.mouseRelease(canvas, Qt.LeftButton, pos=end)
    app.processEvents()

    assert selected == [(3, 9)]
    assert panned == []
    assert canvas._validation_range == (3, 9)
    canvas.close()
