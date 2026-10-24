"""V5 selected-source contracts without a live broker."""

from datetime import datetime, timedelta
import json
from zoneinfo import ZoneInfo
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import gui_indicator_follow_signal_validation_v5_flow as flow_module


@pytest.fixture(autouse=True)
def _scoped_m1_diagnostic_file(tmp_path, monkeypatch):
    monkeypatch.setattr(flow_module, "_SOR_AL_M1_DIAGNOSTIC_PATH",
                        tmp_path / "v5_sor_al_m1.jsonl", raising=False)


def _m1_events():
    path = flow_module._SOR_AL_M1_DIAGNOSTIC_PATH
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_m1_diagnostic_scrubs_session_and_untrusted_reason():
    flow_module._trace_sor_al_m1(
        "callback_terminal", "005930", login_session_id="secret-session",
        reason="BROKER_FAILED secret-session", connection_epoch=7,
    )
    record = _m1_events()[0]
    assert record["event"] == "callback_terminal"
    assert record["code"] == "005930"
    assert record["connection_epoch"] == 7
    assert record["reason"] == "UNCLASSIFIED"
    assert "secret-session" not in json.dumps(record)
from candle_timeframe_aggregation import candle_market_datetime
from kiwoom_api import KiwoomApi
from indicator_follow_signal_validation_historical_cache import IndicatorFollowSignalValidationHistoricalCache
from routines.지표추종매매.routine_validation_contract import ValidationStockRef
from routines.지표추종매매.routine_validation_replay import _latest_candle_is_forming


@pytest.mark.parametrize(
    ("hour", "minute"),
    [(8, 12), (9, 12), (16, 12)],
)
def test_current_m1_is_forming_in_every_active_session(hour, minute):
    now = datetime(2026, 10, 19, hour, minute, 30, tzinfo=ZoneInfo("Asia/Seoul"))
    stamp = now.strftime("%Y%m%d%H%M00")
    assert _latest_candle_is_forming(stamp, 1, timeframe_key="M1", as_of=now)
    assert not _latest_candle_is_forming(stamp, 5, timeframe_key="M5", as_of=now)


@pytest.mark.parametrize(
    ("records", "expected"),
    [
        ([{"code": "005930", "nxt_available": True}], True),
        ([{"code": "005930", "nxt_available": False}], False),
        ([{"code": "005930", "nxt_available": None}], None),
        ([{"code": "005930", "nxt_available": "False"}], None),
        ([], None),
        ([{"code": "005930", "nxt_available": True}] * 2, None),
    ],
)
def test_nxt_source_requires_one_verified_boolean_record(records, expected):
    snapshot = SimpleNamespace(state=flow_module.STOCK_LIBRARY_READY, records=records)
    with patch.object(flow_module, "load_stock_library_snapshot", return_value=snapshot):
        actual = flow_module.IndicatorFollowSignalValidationV5Flow._verified_nxt_validation_stock(
            ValidationStockRef("005930", "Samsung")
        )
    assert actual is expected


@pytest.mark.parametrize("integrated", [True, False])
def test_selected_minute_request_uses_one_broker_source(integrated):
    calls = []
    source = "005930_AL" if integrated else "005930"
    kind = "INTEGRATED" if integrated else "KRX"

    class Broker:
        def request_minute_candles_display_only_integrated(self, code, name, **kwargs):
            calls.append(("AL", code, kwargs["interval"], kwargs.get("nxt_available")))
            kwargs["callback"](_response(source, kind, "M5", "req-1"))
            return {"ok": True, "rqname": "req-1"}

        def request_minute_candles_read_only(self, code, name, **kwargs):
            calls.append(("KRX", code, kwargs["interval"], None))
            kwargs["callback"](_response(source, kind, "M5", "req-1"))
            return {"ok": True, "rqname": "req-1"}

    if not integrated:
        with pytest.raises(ValueError, match="SOR_SOURCE_UNAVAILABLE"):
            flow_module._V5SelectedSourceRequester(
                Broker(), ValidationStockRef("005930", "Samsung"), "M5", False,
            )
        assert calls == []
        return
    requester = flow_module._V5SelectedSourceRequester(
        Broker(), ValidationStockRef("005930", "Samsung"), "M5", True
    )
    results = []
    requester.request_minute_candles_read_only(
        "005930", "Samsung", interval=5, count=5900, callback=results.append
    )
    assert calls == [("AL" if integrated else "KRX", "005930", 5, True if integrated else None)]
    assert results[0]["ok"] is True


def test_v5_requester_rejects_legacy_krx_selection_before_broker_call():
    calls = []
    broker = SimpleNamespace(
        request_minute_candles_read_only=lambda *args, **kwargs: calls.append("KRX"),
        request_minute_candles_display_only_integrated=lambda *args, **kwargs: calls.append("AL"),
    )
    with pytest.raises(ValueError, match="SOR_SOURCE_UNAVAILABLE"):
        flow_module._V5SelectedSourceRequester(
            broker, ValidationStockRef("005930", "Samsung"), "M5", False,
        )
    assert calls == []


def _registered_m1_calculation_case():
    from copy import deepcopy
    from PyQt5.QtCore import QObject
    from gui_market_data_host import MarketDataHost
    from tests.test_sor_core_m1_input import NOW, _installed_host
    from tests.test_indicator_follow_signal_validation_v5_integrated_history_scroll import (
        _flow, _session,
    )

    market = _installed_host()
    market._sor_m1_required_by_stock = {"005930": 5}
    market.project_broker_sor_m1_calculation = lambda **kwargs: (
        MarketDataHost.project_broker_sor_m1_calculation(market, **kwargs)
    )
    original_cache = deepcopy(market._display_timeframe_candles)
    owner = QObject()
    owner.existing_market_data_host = lambda: market
    flow = _flow()
    flow.setParent(owner)
    flow._fixture_owner = owner
    flow._now_factory = lambda: NOW
    session = _session()
    flow._validation_source_by_code[session.request.stock.code] = True
    raw = [{
        "time": f"20261020090{i}00", "open": 900 + i,
        "high": 910 + i, "low": 890 + i, "close": 903 + i,
        "volume": 1,
    } for i in range(5)]
    pool = {
        "stock": session.request.stock, "candles": raw,
        "market_source": "INTEGRATED", "market_data_identity": "005930_AL",
        "timeframe_key": "M1", "timeframe_minutes": 1,
        "requested_count": 5, "request_id": "selected-al-m1",
    }
    return flow, session, pool, market, original_cache


def test_off_session_v5_lease_fetches_missing_al_m1_once_and_retries_after_verified_install():
    flow, session, pool, market, cached = _registered_m1_calculation_case()
    flow._now_factory = lambda: datetime(
        2026, 10, 20, 23, 6, tzinfo=ZoneInfo("Asia/Seoul"),
    )
    market._display_timeframe_candles.pop(("005930", 1))
    market._display_timeframe_session_identity.pop(("005930", 1))
    market.set_v5_observation_lease = lambda _key, _code: {"ok": True}
    requests = []
    retries = []
    window = SimpleNamespace(
        stock=session.request.stock,
        _normal_base_requested=True,
        request_initial_validation=lambda: retries.append("history-revalidated"),
    )
    flow._open_windows[id(window)] = window

    def capture_request(**kwargs):
        requests.append(kwargs)
        return {"ok": True}

    with patch(
        "auto_candle_refresh.request_display_timeframe_candles_for_stock",
        side_effect=capture_request,
    ), patch.object(flow_module.QTimer, "singleShot", side_effect=lambda ms, callback: callback() if ms == 0 else None):
        flow._sync_v5_observation_lease(window)
        flow._sync_v5_observation_lease(window)
        assert len(requests) == 1
        assert requests[0]["code"] == "005930"
        assert requests[0]["timeframe_minutes"] == 1
        assert requests[0]["nxt_available"] is True
        assert retries == []

        market._display_timeframe_candles[("005930", 1)] = cached[("005930", 1)]
        market._display_timeframe_session_identity[("005930", 1)] = (1, "SESSION-1")
        requests[0]["on_terminal"]({"ok": True, "install_result": {"ok": True}})
    assert retries == ["history-revalidated"]


def test_offsession_m1_diagnostic_records_skip_dispatch_and_timeout():
    flow, window, market, _cached, requests, timers, _reasons, _revalidated = (
        _offsession_accepted_request_case()
    )
    flow._validation_source_by_code["005930"] = False
    with patch("auto_candle_refresh.request_display_timeframe_candles_for_stock",
               side_effect=lambda **kwargs: requests.append(kwargs) or {"ok": True}), patch.object(
                   flow_module.QTimer, "singleShot", side_effect=lambda ms, fn: timers.append((ms, fn))):
        flow._sync_v5_observation_lease(window)
        flow._sync_v5_observation_lease(window)
        assert requests == []
        flow._validation_source_by_code["005930"] = True
        flow._sync_v5_observation_lease(window)
        timers.pop()[1]()
    events = _m1_events()
    assert [(e["event"], e.get("reason")) for e in events if e["event"] == "request_skipped"] == [
        ("request_skipped", "SOURCE_UNVERIFIED")]
    assert any(e["event"] == "request_dispatched" and e["count"] > 0 for e in events)
    assert any(e["event"] == "callback_timeout" and e["attempt"] == 1 for e in events)


def test_m1_diagnostic_records_missing_stock_prerequisite():
    flow, window, market, _cached, _requests, _timers, _reasons, _revalidated = (
        _offsession_accepted_request_case()
    )
    window.stock = None
    flow._sync_v5_observation_lease(window)
    assert any(e["event"] == "request_skipped" and e["reason"] == "NO_STOCK"
               for e in _m1_events())


def test_offsession_m1_diagnostic_records_rejection_and_callback_terminal():
    flow, window, market, _cached, requests, timers, _reasons, _revalidated = (
        _offsession_accepted_request_case()
    )
    with patch("auto_candle_refresh.request_display_timeframe_candles_for_stock",
               side_effect=lambda **kwargs: requests.append(kwargs) or {"ok": False, "reason_code": "BROKER_FAILED"}), patch.object(
                   flow_module.QTimer, "singleShot", side_effect=lambda ms, fn: timers.append((ms, fn))):
        flow._sync_v5_observation_lease(window)
        assert len(requests) == 1
    events = _m1_events()
    assert any(e["event"] == "immediate_rejection" and e["reason"] == "BROKER_FAILED"
               for e in events)
    assert any(e["event"] == "callback_terminal" and e["installed"] is False
               for e in events)


def test_offsession_m1_invalid_immediate_result_finishes_attempt():
    flow, window, market, _cached, requests, timers, _reasons, _revalidated = (
        _offsession_accepted_request_case()
    )
    with patch("auto_candle_refresh.request_display_timeframe_candles_for_stock",
               side_effect=lambda **kwargs: requests.append(kwargs) or None), patch.object(
                   flow_module.QTimer, "singleShot", side_effect=lambda ms, fn: timers.append((ms, fn))):
        flow._sync_v5_observation_lease(window)
    assert len(requests) == 1
    assert flow._offsession_sor_m1_requests[id(window)]["pending"] is False
    assert [ms for ms, _ in timers] == [1000]
    assert any(e["event"] == "immediate_rejection" and e["reason"] == "INVALID_START_RESULT"
               for e in _m1_events())


def test_cold_weekend_m3_displays_completed_direct_al_without_m1_signal_proof():
    flow, _window, pool, _event = _observed_v5_case("M3", 3)
    session = flow._validation_sessions[id(_window)]
    market = flow.parent().existing_market_data_host()
    market._display_timeframe_candles.pop(("005930", 1))
    market._display_timeframe_session_identity.pop(("005930", 1))
    pool["candles"] = [
        dict(row, time=f"2026102319{45 + 3 * i:02d}00", is_complete=True)
        for i, row in enumerate(pool["candles"])
    ]
    flow._now_factory = lambda: datetime(2026, 10, 24, 10, 3, tzinfo=ZoneInfo("Asia/Seoul"))
    frozen = flow._sor_m1_calculation_snapshot(session, pool)
    assert frozen[2]["availability_state"] == "SOR_DIRECT_HISTORY_UNAVAILABLE"
    view = dict(pool, display_candles=list(pool["candles"]))
    assert flow._sor_m1_calculation_view(
        session, pool, view, frozen, normal_base_only=True,
    ) is view
    assert flow._sor_m1_calculation_view(session, pool, view, frozen) is view


def test_cold_weekend_m3_routes_validation_to_indicator_only_prepare():
    flow, window, pool, _event = _observed_v5_case("M3", 3)
    market = flow.parent().existing_market_data_host()
    market._display_timeframe_candles.pop(("005930", 1))
    market._display_timeframe_session_identity.pop(("005930", 1))
    pool["candles"] = [
        dict(row, time=f"2026102319{45 + 3 * i:02d}00", is_complete=True)
        for i, row in enumerate(pool["candles"])
    ]
    flow._now_factory = lambda: datetime(2026, 10, 24, 10, 3, tzinfo=ZoneInfo("Asia/Seoul"))
    key = id(window)
    flow._historical_pools[key] = pool
    flow._request_generation[key] = 0
    window._normal_base_requested = False
    request = {
        "window": window, "session": flow._validation_sessions[key], "pool": pool,
        "evaluation_count": 5, "generation": 0, "token": object(),
    }
    with patch.object(flow, "_start_normal_base_prepare") as normal, patch.object(
        flow, "_replay_factory", side_effect=AssertionError("signal replay started")
    ):
        flow._start_pool_prepare(request)
    normal.assert_called_once()
    assert request["normal_base_only"] is True


@pytest.mark.parametrize("invalid", ["identity", "forming", "today", "display", "coverage"])
def test_cold_weekend_m3_rejects_unproven_direct_history(invalid):
    flow, window, pool, _event = _observed_v5_case("M3", 3)
    session = flow._validation_sessions[id(window)]
    market = flow.parent().existing_market_data_host()
    market._display_timeframe_candles.pop(("005930", 1))
    market._display_timeframe_session_identity.pop(("005930", 1))
    pool["candles"] = [
        dict(row, time=f"2026102319{45 + 3 * i:02d}00", is_complete=True)
        for i, row in enumerate(pool["candles"])
    ]
    flow._now_factory = lambda: datetime(2026, 10, 24, 10, 3, tzinfo=ZoneInfo("Asia/Seoul"))
    frozen = flow._sor_m1_calculation_snapshot(session, pool)
    view = dict(pool, display_candles=list(pool["candles"]))
    if invalid == "identity":
        view["market_data_identity"] = "005930"
    elif invalid == "forming":
        view["candles"] = [*view["candles"][:-1], dict(view["candles"][-1], is_complete=False)]
    elif invalid == "today":
        view["candles"] = [*view["candles"][:-1], dict(view["candles"][-1], time="20261024100000")]
    elif invalid == "display":
        view["display_candles"] = view["display_candles"][:-1]
    else:
        view["source_coverage_sufficient"] = False
    with pytest.raises(ValueError, match="SOR_M1_BLOCKED"):
        flow._sor_m1_calculation_view(
            session, pool, view, frozen, normal_base_only=True,
        )


def test_offsession_m1_failure_retries_once_then_stops():
    flow, session, _pool, market, _cached = _registered_m1_calculation_case()
    flow._now_factory = lambda: datetime(2026, 10, 24, 10, 3, tzinfo=ZoneInfo("Asia/Seoul"))
    market._display_timeframe_candles.pop(("005930", 1))
    market._display_timeframe_session_identity.pop(("005930", 1))
    market.set_v5_observation_lease = lambda _key, _code: {"ok": True}
    window = SimpleNamespace(stock=session.request.stock, _normal_base_requested=True)
    flow._open_windows[id(window)] = window
    requests, timers = [], []
    reasons = []
    flow.validation_failed.connect(reasons.append)
    with patch("auto_candle_refresh.request_display_timeframe_candles_for_stock",
               side_effect=lambda **kwargs: requests.append(kwargs) or {"ok": True}), patch.object(
                   flow_module.QTimer, "singleShot", side_effect=lambda ms, fn: timers.append((ms, fn))):
        flow._sync_v5_observation_lease(window)
        requests[0]["on_terminal"]({"ok": False, "reason_code": "DISPLAY_TIMEFRAME_SESSION_UNAVAILABLE"})
        assert [ms for ms, _ in timers] == [30000, 1000]
        delay, retry = timers.pop()
        assert delay == 1000
        flow._sync_v5_observation_lease(window)
        assert len(requests) == 1
        retry()
        assert len(requests) == 2
        requests[1]["on_terminal"]({"ok": False, "reason_code": "BROKER_FAILED"})
        assert [ms for ms, _ in timers] == [30000, 30000]
        flow._request_offsession_sor_m1_once(window, market, "005930")
        assert len(requests) == 2
    assert reasons == ["SOR_M1_ACQUISITION_FAILED:BROKER_FAILED"]


def test_offsession_m1_absent_broker_session_has_finite_diagnostic_poll():
    flow, session, _pool, market, _cached = _registered_m1_calculation_case()
    flow._now_factory = lambda: datetime(2026, 10, 24, 10, 3, tzinfo=ZoneInfo("Asia/Seoul"))
    market._display_timeframe_candles.pop(("005930", 1))
    market.kiwoom_api.broker_session_snapshot = lambda: None
    market.set_v5_observation_lease = lambda _key, _code: {"ok": True}
    window = SimpleNamespace(stock=session.request.stock)
    flow._open_windows[id(window)] = window
    reasons, timers = [], []
    flow.validation_failed.connect(reasons.append)
    with patch("auto_candle_refresh.request_display_timeframe_candles_for_stock") as request, patch.object(
        flow_module.QTimer, "singleShot", side_effect=lambda ms, fn: timers.append((ms, fn))
    ):
        flow._sync_v5_observation_lease(window)
        for _ in range(3):
            assert len(timers) == 1
            timers.pop()[1]()
        request.assert_not_called()
    assert reasons == ["SOR_M1_ACQUISITION_FAILED:SESSION_UNAVAILABLE"]


def test_offsession_m1_late_session_and_cancellation_guards():
    flow, session, _pool, market, _cached = _registered_m1_calculation_case()
    flow._now_factory = lambda: datetime(2026, 10, 24, 10, 3, tzinfo=ZoneInfo("Asia/Seoul"))
    market._display_timeframe_candles.pop(("005930", 1))
    market._display_timeframe_session_identity.pop(("005930", 1))
    market.set_v5_observation_lease = lambda _key, _code: {"ok": True}
    identity = market.kiwoom_api.broker_session_snapshot
    market.kiwoom_api.broker_session_snapshot = lambda: None
    window = SimpleNamespace(stock=session.request.stock)
    flow._open_windows[id(window)] = window
    requests, timers = [], []
    with patch("auto_candle_refresh.request_display_timeframe_candles_for_stock",
               side_effect=lambda **kwargs: requests.append(kwargs) or {"ok": True}), patch.object(
                   flow_module.QTimer, "singleShot", side_effect=lambda ms, fn: timers.append((ms, fn))):
        flow._sync_v5_observation_lease(window)
        assert requests == [] and len(timers) == 1
        market.kiwoom_api.broker_session_snapshot = identity
        timers.pop()[1]()
        assert len(requests) == 1
        requests[0]["on_terminal"]({"ok": False, "reason_code": "BROKER_FAILED"})
        assert [ms for ms, _ in timers] == [30000, 1000]
        flow._open_windows.pop(id(window))
        timers.pop()[1]()
        assert len(requests) == 1


@pytest.mark.parametrize("change", ["stock", "session"])
def test_offsession_m1_stale_request_cannot_revalidate_new_stock_or_session(change):
    flow, session, _pool, market, cached = _registered_m1_calculation_case()
    flow._now_factory = lambda: datetime(2026, 10, 24, 10, 3, tzinfo=ZoneInfo("Asia/Seoul"))
    market._display_timeframe_candles.pop(("005930", 1))
    market._display_timeframe_session_identity.pop(("005930", 1))
    market.set_v5_observation_lease = lambda _key, _code: {"ok": True}
    published = []
    window = SimpleNamespace(
        stock=session.request.stock, _normal_base_requested=True,
        request_initial_validation=lambda: published.append("revalidated"),
    )
    flow._open_windows[id(window)] = window
    requests, timers = [], []
    with patch("auto_candle_refresh.request_display_timeframe_candles_for_stock",
               side_effect=lambda **kwargs: requests.append(kwargs) or {"ok": True}), patch.object(
                   flow_module.QTimer, "singleShot", side_effect=lambda ms, fn: timers.append((ms, fn))):
        flow._sync_v5_observation_lease(window)
        if change == "stock":
            window.stock = ValidationStockRef("000660", "SK Hynix")
        else:
            market.kiwoom_api.broker_session_snapshot = lambda: SimpleNamespace(
                connected=True, connection_epoch=2, login_session_id="SESSION-2",
            )
        market._display_timeframe_candles[("005930", 1)] = cached[("005930", 1)]
        market._display_timeframe_session_identity[("005930", 1)] = (1, "SESSION-1")
        requests[0]["on_terminal"]({"ok": True, "install_result": {"ok": True}})
    assert [ms for ms, _ in timers] == [30000]
    assert published == []
    if change == "session":
        assert any(e["event"] == "callback_ignored" and e["epoch_match"] is False
                   for e in _m1_events())


def _offsession_accepted_request_case():
    flow, session, _pool, market, cached = _registered_m1_calculation_case()
    flow._now_factory = lambda: datetime(2026, 10, 24, 10, 3, tzinfo=ZoneInfo("Asia/Seoul"))
    market._display_timeframe_candles.pop(("005930", 1))
    market._display_timeframe_session_identity.pop(("005930", 1))
    market.set_v5_observation_lease = lambda _key, _code: {"ok": True}
    revalidated = []
    window = SimpleNamespace(
        stock=session.request.stock, _normal_base_requested=True,
        request_initial_validation=lambda: revalidated.append("revalidated"),
    )
    flow._open_windows[id(window)] = window
    requests, timers, reasons = [], [], []
    flow.validation_failed.connect(reasons.append)
    return flow, window, market, cached, requests, timers, reasons, revalidated


def test_offsession_m1_accepted_request_times_out_once_without_overlap():
    flow, window, market, _cached, requests, timers, reasons, revalidated = (
        _offsession_accepted_request_case()
    )
    with patch("auto_candle_refresh.request_display_timeframe_candles_for_stock",
               side_effect=lambda **kwargs: requests.append(kwargs) or {"ok": True}), patch.object(
                   flow_module.QTimer, "singleShot", side_effect=lambda ms, fn: timers.append((ms, fn))):
        flow._sync_v5_observation_lease(window)
        assert [ms for ms, _ in timers] == [30000]
        flow._sync_v5_observation_lease(window)
        assert len(requests) == 1
        timers.pop()[1]()
        assert flow._offsession_sor_m1_requests[id(window)]["pending"] is False
        flow._sync_v5_observation_lease(window)
        flow._request_offsession_sor_m1_once(window, market, "005930")
        assert len(requests) == 1
    assert reasons == ["SOR_M1_ACQUISITION_FAILED:CALLBACK_TIMEOUT"]
    assert revalidated == []


def test_offsession_m1_terminal_before_watchdog_revalidates_once():
    flow, window, market, cached, requests, timers, reasons, revalidated = (
        _offsession_accepted_request_case()
    )
    with patch("auto_candle_refresh.request_display_timeframe_candles_for_stock",
               side_effect=lambda **kwargs: requests.append(kwargs) or {"ok": True}), patch.object(
                   flow_module.QTimer, "singleShot", side_effect=lambda ms, fn: timers.append((ms, fn))):
        flow._sync_v5_observation_lease(window)
        market._display_timeframe_candles[("005930", 1)] = cached[("005930", 1)]
        market._display_timeframe_session_identity[("005930", 1)] = (1, "SESSION-1")
        requests[0]["on_terminal"]({"ok": True, "install_result": {"ok": True}})
        assert [ms for ms, _ in timers] == [30000, 0]
        timers.pop()[1]()
        timers.pop()[1]()
    assert revalidated == ["revalidated"]
    assert reasons == []
    assert len(requests) == 1


def test_offsession_direct_al_m1_persists_full_completed_broker_history(tmp_path):
    from tests.test_display_only_integrated_history import _opt10080_row

    flow, window, market, _cached, requests, timers, reasons, _revalidated = (
        _offsession_accepted_request_case()
    )
    flow._historical_cache = IndicatorFollowSignalValidationHistoricalCache(tmp_path)
    rows = [_opt10080_row("20261023195800", 100),
            _opt10080_row("20261023195900", 110)]
    with patch("auto_candle_refresh.request_display_timeframe_candles_for_stock",
               side_effect=lambda **kwargs: requests.append(kwargs) or {"ok": True}), patch.object(
                   flow_module.QTimer, "singleShot", side_effect=lambda ms, fn: timers.append((ms, fn))):
        flow._sync_v5_observation_lease(window)
        # The display host may retain only its last required M1 bar.
        market._display_timeframe_candles[("005930", 1)] = ({
            "bar_time": "2026-10-23T19:59:00+09:00", "open": 110.0,
            "high": 112.0, "low": 108.0, "close": 111.0, "volume": 10.0,
            "is_complete": True, "display_only": True,
            "market_data_identity": "005930_AL", "market_source": "INTEGRATED",
        },)
        market._display_timeframe_session_identity[("005930", 1)] = (1, "SESSION-1")
        requests[0]["on_terminal"]({
            "ok": True, "code": "005930", "interval": 1, "rows": rows,
            "market_data_identity": "005930_AL", "market_source": "INTEGRATED",
            "display_only": True, "connection_epoch": 1,
            "login_session_id": "SESSION-1", "install_result": {"ok": True},
        })
        flow._request_offsession_sor_m1_once(window, market, "005930")
        assert len(requests) == 1
    entry = flow._cached_history(window.stock, 1, "M1", requests[0]["count"])
    assert entry is not None
    assert [row["time"] for row in entry.candles] == [
        "20261023195800", "20261023195900",
    ]
    assert entry.candles[-1]["close"] == 111.0
    assert reasons == []
    events = _m1_events()
    assert any(e["event"] == "callback_terminal" and e["installed"] is True
               and e["source_match"] is True and e["epoch_match"] is True
               and e["received_count"] == 2
               and e["last_minute"] == "2026-10-23T19:59+09:00" for e in events)
    assert any(e["event"] == "persist_result" and e["persisted"] is True
               and e["count"] == 2 and e["last_minute"] == "2026-10-23T19:59+09:00"
               for e in events)
    assert "SESSION-1" not in flow_module._SOR_AL_M1_DIAGNOSTIC_PATH.read_text(encoding="utf-8")


def test_offsession_existing_complete_session_m1_persists_without_broker_request(tmp_path):
    from gui_market_data_host import MarketDataHost

    flow, session, _pool, market, _cached = _registered_m1_calculation_case()
    flow._historical_cache = IndicatorFollowSignalValidationHistoricalCache(tmp_path)
    flow._now_factory = lambda: datetime(2026, 10, 24, 10, 3, tzinfo=ZoneInfo("Asia/Seoul"))
    window = SimpleNamespace(stock=session.request.stock)
    flow._open_windows[id(window)] = window
    with patch.object(flow._historical_cache, "store_if_lease_current",
                      wraps=flow._historical_cache.store_if_lease_current) as store, patch.object(
        MarketDataHost, "_full_day_display_count", return_value=5), patch(
        "auto_candle_refresh.request_display_timeframe_candles_for_stock"
    ) as request:
        flow._request_offsession_sor_m1_once(window, market, "005930")
        flow._request_offsession_sor_m1_once(window, market, "005930")
        request.assert_not_called()
        assert store.call_count == 1
    entry = flow._cached_history(window.stock, 1, "M1", 5)
    assert entry is not None
    assert [row["time"] for row in entry.candles] == [
        f"20261020090{i}00" for i in range(5)
    ]
    assert any(e["event"] == "persist_result" and e["persisted"] is True
               for e in _m1_events())


def test_offsession_existing_short_session_m1_requests_full_direct_history(tmp_path):
    flow, session, _pool, market, _cached = _registered_m1_calculation_case()
    flow._historical_cache = IndicatorFollowSignalValidationHistoricalCache(tmp_path)
    flow._now_factory = lambda: datetime(2026, 10, 24, 10, 3, tzinfo=ZoneInfo("Asia/Seoul"))
    window = SimpleNamespace(stock=session.request.stock)
    flow._open_windows[id(window)] = window
    requests, timers, failures = [], [], []
    flow.validation_failed.connect(failures.append)
    with patch("auto_candle_refresh.request_display_timeframe_candles_for_stock",
               side_effect=lambda **kwargs: requests.append(kwargs) or {"ok": True}), patch.object(
                   flow_module.QTimer, "singleShot", side_effect=lambda ms, fn: timers.append((ms, fn))):
        flow._request_offsession_sor_m1_once(window, market, "005930")
        assert len(requests) == 1
        assert requests[0]["count"] > len(market._display_timeframe_candles[("005930", 1)])
        assert requests[0]["nxt_available"] is True
        assert flow._cached_history(window.stock, 1, "M1", requests[0]["count"]) is None
        assert [ms for ms, _ in timers] == [30000]
        timers[0][1]()
    assert failures == ["SOR_M1_ACQUISITION_FAILED:CALLBACK_TIMEOUT"]


def test_offsession_existing_short_session_m1_persists_full_direct_reply(tmp_path):
    from tests.test_display_only_integrated_history import _opt10080_row

    flow, session, _pool, market, _cached = _registered_m1_calculation_case()
    flow._historical_cache = IndicatorFollowSignalValidationHistoricalCache(tmp_path)
    flow._now_factory = lambda: datetime(2026, 10, 24, 10, 3, tzinfo=ZoneInfo("Asia/Seoul"))
    window = SimpleNamespace(stock=session.request.stock)
    flow._open_windows[id(window)] = window
    requests = []
    with patch("auto_candle_refresh.request_display_timeframe_candles_for_stock",
               side_effect=lambda **kwargs: requests.append(kwargs) or {"ok": True}), patch.object(
                   flow_module.QTimer, "singleShot"):
        flow._request_offsession_sor_m1_once(window, market, "005930")
        market._display_timeframe_candles[("005930", 1)] = ({
            "bar_time": "2026-10-23T19:59:00+09:00", "open": 110.0,
            "high": 112.0, "low": 108.0, "close": 111.0, "volume": 10.0,
            "is_complete": True, "display_only": True,
            "market_data_identity": "005930_AL", "market_source": "INTEGRATED",
        },)
        requests[0]["on_terminal"]({
            "ok": True, "code": "005930", "interval": 1,
            "rows": [_opt10080_row("20261023195900", 110)],
            "market_data_identity": "005930_AL", "market_source": "INTEGRATED",
            "display_only": True, "connection_epoch": 1,
            "login_session_id": "SESSION-1", "install_result": {"ok": True},
        })
        flow._request_offsession_sor_m1_once(window, market, "005930")
        assert len(requests) == 1
    entry = flow._cached_history(window.stock, 1, "M1", requests[0]["count"])
    assert entry is not None and entry.candles[-1]["time"] == "20261023195900"


@pytest.mark.parametrize("invalid", ["source", "bar_session", "future", "forming", "identity"])
def test_offsession_existing_invalid_session_m1_never_persists(tmp_path, invalid):
    from gui_market_data_host import MarketDataHost

    flow, session, _pool, market, _cached = _registered_m1_calculation_case()
    flow._historical_cache = IndicatorFollowSignalValidationHistoricalCache(tmp_path)
    flow._now_factory = lambda: datetime(2026, 10, 24, 10, 3, tzinfo=ZoneInfo("Asia/Seoul"))
    key = ("005930", 1)
    rows = list(market._display_timeframe_candles[key])
    if invalid == "identity":
        market._display_timeframe_session_identity[key] = (2, "SESSION-2")
    else:
        field, value = {
            "source": ("market_data_identity", "005930"),
            "bar_session": ("session", "NXT_AFTER"),
            "future": ("bar_time", "2026-10-24T10:03:00+09:00"),
            "forming": ("is_complete", False),
        }[invalid]
        rows[-1] = dict(rows[-1], **{field: value})
        market._display_timeframe_candles[key] = tuple(rows)
    window = SimpleNamespace(stock=session.request.stock)
    flow._open_windows[id(window)] = window
    with patch.object(MarketDataHost, "_full_day_display_count", return_value=5), patch(
        "auto_candle_refresh.request_display_timeframe_candles_for_stock", return_value={"ok": True}
    ) as request, patch.object(flow_module.QTimer, "singleShot"):
        flow._request_offsession_sor_m1_once(window, market, "005930")
        request.assert_called_once()
    assert flow._cached_history(window.stock, 1, "M1", 5) is None


def test_offsession_existing_session_m1_rejected_cache_lease_does_not_persist(tmp_path):
    from gui_market_data_host import MarketDataHost

    flow, session, _pool, market, _cached = _registered_m1_calculation_case()
    flow._historical_cache = IndicatorFollowSignalValidationHistoricalCache(tmp_path)
    flow._now_factory = lambda: datetime(2026, 10, 24, 10, 3, tzinfo=ZoneInfo("Asia/Seoul"))
    window = SimpleNamespace(stock=session.request.stock)
    flow._open_windows[id(window)] = window
    with patch.object(MarketDataHost, "_full_day_display_count", return_value=5), patch.object(
        flow._historical_cache, "store_if_lease_current", return_value=False
    ), patch("auto_candle_refresh.request_display_timeframe_candles_for_stock",
             return_value={"ok": True}) as request, patch.object(flow_module.QTimer, "singleShot"):
        flow._request_offsession_sor_m1_once(window, market, "005930")
        request.assert_called_once()
    assert flow._cached_history(window.stock, 1, "M1", 5) is None


@pytest.mark.parametrize("invalid", ["source", "session", "failed", "forming"])
def test_offsession_m1_unverified_terminal_does_not_persist(tmp_path, invalid):
    from tests.test_display_only_integrated_history import _opt10080_row

    flow, window, market, _cached, requests, timers, _reasons, _revalidated = (
        _offsession_accepted_request_case()
    )
    flow._historical_cache = IndicatorFollowSignalValidationHistoricalCache(tmp_path)
    terminal = {
        "ok": True, "code": "005930", "interval": 1,
        "rows": [_opt10080_row("20261023195900", 110)],
        "market_data_identity": "005930_AL", "market_source": "INTEGRATED",
        "display_only": True, "connection_epoch": 1,
        "login_session_id": "SESSION-1", "install_result": {"ok": True},
    }
    if invalid == "source":
        terminal["market_data_identity"] = "005930"
    elif invalid == "session":
        terminal["login_session_id"] = "SESSION-2"
    elif invalid == "failed":
        terminal["ok"] = False
    else:
        terminal["rows"] = [_opt10080_row("20261024100300", 110)]
    with patch("auto_candle_refresh.request_display_timeframe_candles_for_stock",
               side_effect=lambda **kwargs: requests.append(kwargs) or {"ok": True}), patch.object(
                   flow_module.QTimer, "singleShot", side_effect=lambda ms, fn: timers.append((ms, fn))):
        flow._sync_v5_observation_lease(window)
        market._display_timeframe_candles[("005930", 1)] = ({
            "bar_time": "2026-10-23T19:59:00+09:00", "open": 110.0,
            "high": 112.0, "low": 108.0, "close": 111.0, "volume": 10.0,
            "is_complete": True, "display_only": True,
            "market_data_identity": "005930_AL", "market_source": "INTEGRATED",
        },)
        market._display_timeframe_session_identity[("005930", 1)] = (1, "SESSION-1")
        requests[0]["on_terminal"](terminal)
    assert flow._cached_history(window.stock, 1, "M1", requests[0]["count"]) is None
    if invalid in {"source", "session", "forming"}:
        events = _m1_events()
        assert any(e["event"] == "persist_result" and e["persisted"] is False
                   for e in events)
        if invalid == "source":
            assert any(e["event"] == "callback_terminal" and e["source_match"] is False
                       for e in events)
        if invalid == "session":
            assert any(e["event"] == "callback_terminal" and e["epoch_match"] is False
                       for e in events)


def test_offsession_m1_late_terminal_after_timeout_cannot_revalidate():
    flow, window, market, cached, requests, timers, reasons, revalidated = (
        _offsession_accepted_request_case()
    )
    with patch("auto_candle_refresh.request_display_timeframe_candles_for_stock",
               side_effect=lambda **kwargs: requests.append(kwargs) or {"ok": True}), patch.object(
                   flow_module.QTimer, "singleShot", side_effect=lambda ms, fn: timers.append((ms, fn))):
        flow._sync_v5_observation_lease(window)
        timers.pop()[1]()
        market._display_timeframe_candles[("005930", 1)] = cached[("005930", 1)]
        market._display_timeframe_session_identity[("005930", 1)] = (1, "SESSION-1")
        requests[0]["on_terminal"]({"ok": True, "install_result": {"ok": True}})
    assert reasons == ["SOR_M1_ACQUISITION_FAILED:CALLBACK_TIMEOUT"]
    assert revalidated == []
    assert timers == []


def test_offsession_m1_old_watchdog_cannot_timeout_explicit_retry():
    flow, window, market, _cached, requests, timers, reasons, _revalidated = (
        _offsession_accepted_request_case()
    )
    with patch("auto_candle_refresh.request_display_timeframe_candles_for_stock",
               side_effect=lambda **kwargs: requests.append(kwargs) or {"ok": True}), patch.object(
                   flow_module.QTimer, "singleShot", side_effect=lambda ms, fn: timers.append((ms, fn))):
        flow._sync_v5_observation_lease(window)
        first_watchdog = timers.pop()[1]
        requests[0]["on_terminal"]({"ok": False, "reason_code": "BROKER_FAILED"})
        assert [ms for ms, _ in timers] == [1000]
        timers.pop()[1]()
        assert [ms for ms, _ in timers] == [30000]
        first_watchdog()
        assert flow._offsession_sor_m1_requests[id(window)]["pending"] is True
        assert reasons == []
        assert len(requests) == 2
        timers.pop()[1]()
    assert reasons == ["SOR_M1_ACQUISITION_FAILED:CALLBACK_TIMEOUT"]


@pytest.mark.parametrize("change", ["window", "stock", "session"])
def test_offsession_m1_watchdog_ignores_changed_context(change):
    flow, window, market, _cached, requests, timers, reasons, _revalidated = (
        _offsession_accepted_request_case()
    )
    with patch("auto_candle_refresh.request_display_timeframe_candles_for_stock",
               side_effect=lambda **kwargs: requests.append(kwargs) or {"ok": True}), patch.object(
                   flow_module.QTimer, "singleShot", side_effect=lambda ms, fn: timers.append((ms, fn))):
        flow._sync_v5_observation_lease(window)
        assert [ms for ms, _ in timers] == [30000]
        if change == "window":
            flow._open_windows.pop(id(window))
        elif change == "stock":
            window.stock = ValidationStockRef("000660", "SK Hynix")
        else:
            market.kiwoom_api.broker_session_snapshot = lambda: SimpleNamespace(
                connected=True, connection_epoch=2, login_session_id="SESSION-2",
            )
        timers.pop()[1]()
    assert reasons == []
    assert len(requests) == 1


def test_offsession_m1_invalid_al_eligibility_does_not_request():
    flow, session, _pool, market, _cached = _registered_m1_calculation_case()
    flow._now_factory = lambda: datetime(2026, 10, 24, 10, 3, tzinfo=ZoneInfo("Asia/Seoul"))
    market._display_timeframe_candles.pop(("005930", 1))
    market.set_v5_observation_lease = lambda _key, _code: {"ok": True}
    window = SimpleNamespace(stock=session.request.stock)
    flow._open_windows[id(window)] = window
    flow._validation_source_by_code["005930"] = False
    with patch("auto_candle_refresh.request_display_timeframe_candles_for_stock") as request:
        flow._sync_v5_observation_lease(window)
    request.assert_not_called()


def test_weekend_v5_history_uses_last_verified_al_minute_not_weekend_clock():
    flow, session, pool, _market, _cached = _registered_m1_calculation_case()
    flow._now_factory = lambda: datetime(
        2026, 10, 24, 5, 25, tzinfo=ZoneInfo("Asia/Seoul"),
    )
    frozen = flow._sor_m1_calculation_snapshot(session, pool)
    assert frozen is not None
    assert frozen[1].date().isoformat() == "2026-10-20"
    assert frozen[2]["available"] is True


def test_weekend_m1_still_requires_valid_projection():
    flow, session, pool, _market, _cached = _registered_m1_calculation_case()
    flow._now_factory = lambda: datetime(
        2026, 10, 24, 5, 25, tzinfo=ZoneInfo("Asia/Seoul"),
    )
    frozen = flow._sor_m1_calculation_snapshot(session, pool)
    frozen[2]["available"] = False
    with pytest.raises(ValueError, match="SOR_M1_BLOCKED"):
        flow._sor_m1_calculation_view(session, pool, pool, frozen)


def test_active_session_v5_lease_does_not_start_parallel_al_m1_request():
    flow, session, _pool, market, _cached = _registered_m1_calculation_case()
    flow._now_factory = lambda: datetime(
        2026, 10, 20, 9, 6, tzinfo=ZoneInfo("Asia/Seoul"),
    )
    market._display_timeframe_candles.pop(("005930", 1))
    market.set_v5_observation_lease = lambda _key, _code: {"ok": True}
    window = SimpleNamespace(stock=session.request.stock)
    flow._open_windows[id(window)] = window
    with patch(
        "auto_candle_refresh.request_display_timeframe_candles_for_stock",
    ) as request:
        flow._sync_v5_observation_lease(window)
    request.assert_not_called()


def test_v5_gap_error_names_stock_session_and_missing_minute():
    flow, session, pool, market, _ = _registered_m1_calculation_case()
    key = ("005930", 1)
    market._display_timeframe_candles[key] = tuple(
        row for row in market._display_timeframe_candles[key]
        if not row["bar_time"].startswith("2026-10-20T09:02:")
    )
    frozen = flow._sor_m1_calculation_snapshot(session, pool)
    assert frozen[2]["availability_state"] == "SOR_M1_INTERNAL_COVERAGE_GAP"
    with pytest.raises(ValueError) as failure:
        flow._sor_m1_calculation_view(session, pool, pool, frozen)
    assert str(failure.value) == (
        "SOR_M1_BLOCKED:SOR_M1_INTERNAL_COVERAGE_GAP "
        "[stock=005930, session=KRX, between=2026-10-20T09:01:00+09:00"
        "..2026-10-20T09:03:00+09:00, missing=2026-10-20T09:02:00+09:00"
        "..2026-10-20T09:02:00+09:00, slots=1]"
    )
    frozen[2]["gap_detail"] = {}
    with pytest.raises(ValueError, match="^SOR_M1_BLOCKED:SOR_M1_INTERNAL_COVERAGE_GAP$"):
        flow._sor_m1_calculation_view(session, pool, pool, frozen)


def test_accepted_broker_al_forming_m1_emits_only_after_install():
    from gui_market_data_host import MarketDataHost
    from tests.test_display_only_integrated_history import (
        DisplayOnlyMarketDataHostTest, _opt10080_row,
    )
    from tests.test_sor_core_m1_input import NOW, _installed_host

    market = _installed_host()
    events = []
    market.market_data_observed = SimpleNamespace(emit=events.append)
    rows = [_opt10080_row("20261020090500", 110),
            _opt10080_row("20261020090400", 104)]
    with patch("gui_market_data_host.load_stock_library_snapshot",
               return_value=DisplayOnlyMarketDataHostTest._snapshot(True)), patch.object(
                   MarketDataHost, "direct_timeframe_requirements", return_value={1: 5}
               ):
        result = MarketDataHost.install_display_timeframe_candles(
            market, "005930", 1, rows, as_of=NOW,
            market_data_identity="005930_AL", market_source="INTEGRATED",
            display_only=True, connection_epoch=1, login_session_id="SESSION-1",
            request_as_of=(NOW + timedelta(seconds=10)).isoformat(),
            received_at=(NOW + timedelta(seconds=11)).isoformat(),
        )
        assert result["ok"] is True
        assert len(events) == 1
        assert events[0]["source"] == "SOR_AL_M1_SNAPSHOT_ACCEPTED"
        assert events[0]["stock_code"] == "005930"
        assert events[0]["market_data_identity"] == "005930_AL"
        assert events[0]["connection_epoch"] == 1
        assert events[0]["login_session_id"] == "SESSION-1"
        assert events[0]["forming_minute"] == market._sor_forming_m1_by_stock[
            "005930"]["candle"]["bar_time"]
        assert events[0]["snapshot_received_at"] == market._sor_forming_m1_by_stock[
            "005930"]["candle"]["snapshot_received_at"]
        MarketDataHost.install_display_timeframe_candles(
            market, "005930", 1, rows[1:], as_of=NOW,
            market_data_identity="005930_AL", market_source="INTEGRATED",
            display_only=True, connection_epoch=1, login_session_id="SESSION-1",
        )
        MarketDataHost.install_display_timeframe_candles(
            market, "005930", 1, rows, as_of=NOW,
            market_data_identity="005930_AL", market_source="KRX",
            display_only=True, connection_epoch=1, login_session_id="SESSION-1",
        )
    assert len(events) == 1


def test_accepted_broker_al_install_survives_throwing_observer():
    from gui_market_data_host import MarketDataHost
    from tests.test_display_only_integrated_history import (
        DisplayOnlyMarketDataHostTest, _opt10080_row,
    )
    from tests.test_sor_core_m1_input import NOW, _installed_host

    market = _installed_host()

    def throwing_observer(_event):
        raise RuntimeError("optional observer failed")

    market.market_data_observed = SimpleNamespace(emit=throwing_observer)
    rows = [_opt10080_row("20261020090500", 110),
            _opt10080_row("20261020090400", 104)]
    with patch("gui_market_data_host.load_stock_library_snapshot",
               return_value=DisplayOnlyMarketDataHostTest._snapshot(True)), patch.object(
                   MarketDataHost, "direct_timeframe_requirements", return_value={1: 5}
               ):
        result = MarketDataHost.install_display_timeframe_candles(
            market, "005930", 1, rows, as_of=NOW,
            market_data_identity="005930_AL", market_source="INTEGRATED",
            display_only=True, connection_epoch=1, login_session_id="SESSION-1",
            request_as_of=(NOW + timedelta(seconds=10)).isoformat(),
            received_at=(NOW + timedelta(seconds=11)).isoformat(),
        )
    assert result["ok"] is True
    assert result["reason_code"] == "DISPLAY_TIMEFRAME_READY"
    assert market._sor_forming_m1_by_stock["005930"]["candle"][
        "snapshot_source"] == "BROKER_AL_TR_FORMING_M1"


def _observed_v5_case(timeframe="M1", minutes=1):
    flow, session, pool, market, _ = _registered_m1_calculation_case()
    if timeframe != "M1":
        from routines.지표추종매매.routine_validation_contract import (
            ValidationRequest, ValidationSettingsSnapshot,
        )
        from routines.지표추종매매.routine_validation_session import ValidationSession
        settings = ValidationSettingsSnapshot({
            "bar": {"bar_minutes": minutes},
            "validation_timeframe": {"key": timeframe},
        })
        session = ValidationSession(
            ValidationRequest(session.request.stock, settings, minutes),
            operation_active_reader=lambda: False,
        )
        pool.update(timeframe_key=timeframe, timeframe_minutes=minutes)
    callbacks = []
    market.market_data_observed = SimpleNamespace(connect=callbacks.append)
    market._current_display_session_identity = lambda: (1, "SESSION-1")
    candle = {
        "bar_time": "2026-10-20T09:05:00+09:00",
        "snapshot_received_at": "2026-10-20T09:05:11+09:00",
        "snapshot_source": "BROKER_AL_TR_FORMING_M1",
    }
    market._sor_forming_m1_by_stock = {
        "005930": {"session_identity": (1, "SESSION-1"), "candle": candle},
    }
    window = SimpleNamespace(
        stock=session.request.stock, _normal_base_ready=True,
        _normal_base_requested=True, _replay_snapshot=None,
        validation_range=None, _chart_results_suspended=False,
        marking_display_check=SimpleNamespace(isChecked=lambda: False),
        is_chart_interaction_busy=lambda: False,
    )
    key = id(window)
    flow._open_windows[key] = window
    flow._validation_sessions[key] = session
    flow._historical_pools[key] = pool
    flow._validation_source_by_code["005930"] = True
    flow._bind_sor_m1_observations()
    flow._bind_sor_m1_observations()
    assert callbacks == [flow._on_sor_m1_snapshot_accepted]
    event = {
        "source": "SOR_AL_M1_SNAPSHOT_ACCEPTED", "stock_code": "005930",
        "market_data_identity": "005930_AL", "connection_epoch": 1,
        "login_session_id": "SESSION-1",
        "forming_minute": candle["bar_time"],
        "snapshot_received_at": candle["snapshot_received_at"],
    }
    return flow, window, pool, event


@pytest.mark.parametrize("getter_mode", ["missing", "throwing"])
def test_optional_v5_observation_getter_does_not_interrupt_bridge(getter_mode):
    flow, window, _pool, event = _observed_v5_case()
    owner = flow.parent()
    if getter_mode == "missing":
        del owner.existing_market_data_host
    else:
        def throwing_getter():
            raise RuntimeError("optional market host unavailable")

        owner.existing_market_data_host = throwing_getter

    flow._sor_m1_observed_host = None
    flow._bind_sor_m1_observations()
    with patch.object(flow, "_use_pool_for_window") as dispatch:
        flow._on_sor_m1_snapshot_accepted(event)
    dispatch.assert_not_called()
    assert id(window) not in flow._sor_m1_last_snapshot


def test_accepted_native_m1_dispatches_once_without_reloading_selected_history():
    flow, window, pool, event = _observed_v5_case()
    selected_rows = pool["candles"]
    selected_copy = [dict(row) for row in selected_rows]
    dispatched = []
    view = {"candles": selected_rows, "display_candles": selected_rows}
    with patch.object(flow, "_normal_base_history_view", return_value=(view, 0)) as prepare, patch.object(
        flow, "_use_pool_for_window", side_effect=lambda *args, **kwargs: dispatched.append((args, kwargs))
    ):
        flow._on_sor_m1_snapshot_accepted(event)
        flow._on_sor_m1_snapshot_accepted(event)
    assert prepare.call_count == 1
    assert len(dispatched) == 1
    assert dispatched[0][0][0] is window
    assert dispatched[0][0][2] is pool
    assert dispatched[0][1]["_normal_view"] is view
    assert dispatched[0][1]["live_m1_snapshot_key"] == (
        1, "SESSION-1", event["forming_minute"], event["snapshot_received_at"],
    )
    assert pool["candles"] is selected_rows
    assert pool["candles"] == selected_copy
    assert pool["market_data_identity"] == "005930_AL"


@pytest.mark.parametrize("timeframe,minutes", [
    ("M3", 3), ("M5", 5), ("M10", 10), ("M15", 15),
    ("M30", 30), ("M60", 60), ("M120", 120), ("M240", 240),
])
def test_accepted_m1_event_dispatches_selected_higher_minute_once(timeframe, minutes):
    flow, window, pool, event = _observed_v5_case(timeframe, minutes)
    view = {"candles": pool["candles"], "display_candles": pool["candles"]}
    with patch.object(flow, "_normal_base_history_view", return_value=(view, 0)), patch.object(
        flow, "_use_pool_for_window"
    ) as dispatch:
        flow._on_sor_m1_snapshot_accepted(event)
        flow._on_sor_m1_snapshot_accepted(event)
    assert dispatch.call_count == 1
    assert dispatch.call_args.kwargs["live_m1_snapshot_key"] == (
        1, "SESSION-1", event["forming_minute"], event["snapshot_received_at"],
    )
    with patch.object(flow, "_use_pool_for_window") as stale:
        flow._on_sor_m1_snapshot_accepted(dict(event, connection_epoch=2))
    stale.assert_not_called()


@pytest.mark.parametrize("timeframe", ["D1", "W1", "MO1", "Y1"])
def test_accepted_m1_event_dispatches_calendar_once_and_keeps_guards(timeframe):
    flow, window, pool, event = _observed_v5_case(timeframe, 1)
    view = {"candles": pool["candles"], "display_candles": pool["candles"]}
    with patch.object(flow, "_normal_base_history_view", return_value=(view, 0)), patch.object(
        flow, "_use_pool_for_window"
    ) as dispatch:
        flow._on_sor_m1_snapshot_accepted(event)
        flow._on_sor_m1_snapshot_accepted(event)
        assert dispatch.call_count == 1
        assert dispatch.call_args.kwargs["live_m1_snapshot_key"][-1] == event["snapshot_received_at"]
        flow._on_sor_m1_snapshot_accepted(dict(event, connection_epoch=2))
        window.marking_display_check.isChecked = lambda: True
        flow._sor_m1_observed_host._sor_forming_m1_by_stock["005930"]["candle"][
            "snapshot_received_at"] = "2026-10-20T09:05:12+09:00"
        flow._on_sor_m1_snapshot_accepted(dict(event, snapshot_received_at="2026-10-20T09:05:12+09:00"))
        window.marking_display_check.isChecked = lambda: False
        window.validation_range = (1, 2)
        flow._sor_m1_observed_host._sor_forming_m1_by_stock["005930"]["candle"][
            "snapshot_received_at"] = "2026-10-20T09:05:13+09:00"
        flow._on_sor_m1_snapshot_accepted(dict(event, snapshot_received_at="2026-10-20T09:05:13+09:00"))
        assert dispatch.call_count == 1


def test_native_m1_bridge_coalesces_in_existing_prepare_without_history_request():
    flow, window, pool, event = _observed_v5_case()
    key = id(window)
    selected_rows = pool["candles"]
    flow._prepare_active[key] = object()
    view = {"candles": selected_rows, "display_candles": selected_rows}
    with patch.object(flow, "_normal_base_history_view", return_value=(view, 0)), patch.object(
        flow, "_ensure_normal_base_history", side_effect=AssertionError("history requested")
    ), patch.object(flow, "_start_pool_prepare") as start:
        flow._on_sor_m1_snapshot_accepted(event)
        first = flow._prepare_pending[key]
        new_receipt = "2026-10-20T09:05:12+09:00"
        flow._sor_m1_observed_host._sor_forming_m1_by_stock[
            "005930"]["candle"]["snapshot_received_at"] = new_receipt
        flow._on_sor_m1_snapshot_accepted(dict(event, snapshot_received_at=new_receipt))
    start.assert_not_called()
    assert flow._prepare_pending[key] is not first
    assert flow._prepare_pending[key]["normal_base_view"] is view
    assert pool["candles"] is selected_rows
    assert pool["market_data_identity"] == "005930_AL"


def test_native_m1_bridge_updates_each_eligible_open_window_once():
    flow, window, pool, event = _observed_v5_case()
    second = SimpleNamespace(**vars(window))
    flow._open_windows[id(second)] = second
    flow._validation_sessions[id(second)] = flow._validation_sessions[id(window)]
    flow._historical_pools[id(second)] = pool
    view = {"candles": pool["candles"], "display_candles": pool["candles"]}
    with patch.object(flow, "_normal_base_history_view", return_value=(view, 0)), patch.object(
        flow, "_use_pool_for_window"
    ) as dispatch:
        flow._on_sor_m1_snapshot_accepted(event)
        flow._on_sor_m1_snapshot_accepted(event)
    assert dispatch.call_count == 2
    assert {id(call.args[0]) for call in dispatch.call_args_list} == {
        id(window), id(second),
    }


@pytest.mark.parametrize("change", [
    {"source": "REALTIME"}, {"stock_code": "000660"},
    {"market_data_identity": "005930"}, {"login_session_id": "OTHER"},
    {"snapshot_received_at": "2026-10-20T09:05:12+09:00"},
])
def test_native_m1_bridge_rejects_wrong_source_stock_session_or_snapshot(change):
    flow, _window, _pool, event = _observed_v5_case()
    with patch.object(flow, "_use_pool_for_window") as dispatch:
        flow._on_sor_m1_snapshot_accepted(dict(event, **change))
    dispatch.assert_not_called()


@pytest.mark.parametrize("mode", ["marking", "range", "closed", "busy"])
def test_native_m1_bridge_preserves_manual_or_suspended_view(mode):
    flow, window, _pool, event = _observed_v5_case()
    if mode == "marking":
        window.marking_display_check.isChecked = lambda: True
    elif mode == "range":
        window.validation_range = (1, 2)
    elif mode == "closed":
        window._chart_results_suspended = True
    else:
        window.is_chart_interaction_busy = lambda: True
    with patch.object(flow, "_use_pool_for_window") as dispatch:
        flow._on_sor_m1_snapshot_accepted(event)
    dispatch.assert_not_called()


def test_live_m1_projection_appends_forming_bar_without_changing_direct_history():
    from copy import deepcopy
    from gui_market_data_host import MarketDataHost
    from tests.test_sor_core_m1_input import NOW, _forming_host

    flow, session, pool, _market, _ = _registered_m1_calculation_case()
    market = _forming_host()
    market.project_broker_sor_m1_calculation = lambda **kwargs: (
        MarketDataHost.project_broker_sor_m1_calculation(market, **kwargs)
    )
    market._sor_m1_required_by_stock = {"005930": 5}
    flow.parent().existing_market_data_host = lambda: market
    flow._now_factory = lambda: NOW + timedelta(seconds=30)
    before = deepcopy(pool)
    frozen = flow._sor_m1_calculation_snapshot(session, pool, live_forming=True)
    selected = flow._validation_pool_for_session(session, pool, target_count=5)
    view = flow._sor_m1_calculation_view(session, pool, selected, frozen)

    assert frozen[2]["projection"] == "FORMING_BASE_BAR"
    assert [row["time"] for row in view["candles"]][-2:] == [
        "20261020090400", "20261020090500",
    ]
    assert view["candles"][-1]["close"] == 111
    assert view["display_candles"] == view["candles"][-len(before["candles"]):]
    assert view["display_candles"][-1]["time"] == "20261020090500"
    assert pool == before


def test_normal_worker_calls_production_selector_on_forming_m1_only():
    from routines.지표추종매매.routine import evaluate_signal_selection

    flow, session, pool, _market, _ = _registered_m1_calculation_case()
    window = SimpleNamespace(_filter_display_descriptors=lambda _rules: ())
    key = id(window)
    flow._open_windows[key] = window
    flow._validation_sessions[key] = session
    flow._historical_pools[key] = pool
    flow._history_targets[key] = 5
    flow._request_generation[key] = 0
    token = object()
    minute = "2026-10-20T09:05:00+09:00"
    receipt = "2026-10-20T09:05:11+09:00"
    view = dict(pool, candles=[*pool["candles"], {
        "time": "20261020090500", "open": 110, "high": 112,
        "low": 109, "close": 111, "volume": 1,
    }], sor_m1_calculation_complete=True)
    request = {
        "window": window, "session": session, "pool": pool,
        "evaluation_count": 100, "generation": 0, "token": token,
        "dispatch_snapshot": {}, "normal_base_view": view,
        "live_m1_snapshot_key": (1, "SESSION-1", minute, receipt),
        "sor_m1_calculation_snapshot": (None, None, {
            "forming_snapshot_source": "BROKER_AL_TR_FORMING_M1",
            "forming_snapshot_received_at": receipt,
        }),
    }
    emitted = []
    with patch.object(flow, "_normal_base_history_view", return_value=(view, 0)), patch.object(
        flow, "_sor_m1_calculation_view", return_value=view
    ), patch.object(flow_module, "_build_validation_projection_data", return_value=(None, {})), patch.object(
        flow_module, "Thread", side_effect=lambda *, target, **_kwargs: SimpleNamespace(start=target)
    ), patch.object(flow, "_emit_signal_scan_completed", side_effect=emitted.append), patch(
        "routines.지표추종매매.routine.evaluate_signal_selection",
        wraps=evaluate_signal_selection,
    ) as select:
        flow._start_normal_base_prepare(request, 5)

    assert select.call_count == 1
    candles, rules, context = select.call_args.args
    assert candles is context["candles"]
    assert candles[-1]["time"] == "20261020090500"
    assert context["_indicator_follow_validation_current_price"] == 111
    assert rules == session.request.settings_snapshot.to_dict()
    assert emitted[0]["normal_base"] is True
    assert emitted[0]["live_sor_signal"]["stock_code"] == "005930"
    assert emitted[0]["live_sor_signal"]["timeframe_key"] == "M1"

    request["token"] = object()
    krx_view = dict(view, market_source="KRX", market_data_identity="005930")
    with patch.object(flow, "_normal_base_history_view", return_value=(krx_view, 0)), patch.object(
        flow, "_sor_m1_calculation_view", return_value=krx_view
    ), patch.object(flow_module, "_build_validation_projection_data", return_value=(None, {})), patch.object(
        flow_module, "Thread", side_effect=lambda *, target, **_kwargs: SimpleNamespace(start=target)
    ), patch.object(flow, "_emit_signal_scan_completed", side_effect=emitted.append), patch(
        "routines.지표추종매매.routine.evaluate_signal_selection",
    ) as select:
        flow._start_normal_base_prepare(request, 5)
    select.assert_not_called()
    assert emitted[-1]["live_sor_signal"]["unavailable"] is True

    request["token"] = object()
    with patch.object(flow, "_normal_base_history_view", return_value=(view, 0)), patch.object(
        flow, "_sor_m1_calculation_view", return_value=view
    ), patch.object(flow_module, "_build_validation_projection_data", return_value=(None, {})), patch.object(
        flow_module, "Thread", side_effect=lambda *, target, **_kwargs: SimpleNamespace(start=target)
    ), patch.object(flow, "_emit_signal_scan_completed", side_effect=emitted.append), patch(
        "routines.지표추종매매.routine.evaluate_signal_selection",
        side_effect=RuntimeError("filter unavailable"),
    ):
        flow._start_normal_base_prepare(request, 5)
    assert emitted[-1]["normal_base_presentation"] == ((), None)
    assert emitted[-1]["live_sor_signal"]["unavailable"] is True
    assert "error" not in emitted[-1]


@pytest.mark.parametrize("timeframe,minutes,formed_time,minute", [
    ("M3", 3, "20261020090300", "2026-10-20T09:05:00+09:00"),
    ("M5", 5, "20261020154000", "2026-10-20T15:40:00+09:00"),
    ("D1", 1, "20261020000000", "2026-10-20T09:05:00+09:00"),
    ("W1", 1, "20261019000000", "2026-10-20T09:05:00+09:00"),
    ("MO1", 1, "20261001000000", "2026-10-20T09:05:00+09:00"),
    ("Y1", 1, "20260102000000", "2026-10-20T09:05:00+09:00"),
])
def test_normal_worker_evaluates_only_matching_displayed_higher_bucket(
    timeframe, minutes, formed_time, minute,
):
    flow, base_session, base_pool, _market, _ = _registered_m1_calculation_case()
    session = SimpleNamespace(request=SimpleNamespace(
        stock=base_session.request.stock, timeframe_key=timeframe,
        timeframe_minutes=minutes,
        settings_snapshot=SimpleNamespace(
            to_dict=lambda: {"bar": {"bar_minutes": minutes}}),
    ))
    pool = dict(base_pool, timeframe_key=timeframe, timeframe_minutes=minutes)
    formed = {"time": formed_time, "open": 110, "high": 112,
              "low": 109, "close": 111, "volume": 3}
    view = dict(pool, candles=[formed], display_candles=[formed],
                sor_m1_calculation_complete=True)
    window = SimpleNamespace(_filter_display_descriptors=lambda _rules: ())
    key = id(window)
    flow._open_windows[key] = window
    flow._validation_sessions[key] = session
    flow._historical_pools[key] = pool
    flow._history_targets[key] = 5
    flow._request_generation[key] = 0
    receipt = minute.replace(":00+09:00", ":11+09:00")
    request = {
        "window": window, "session": session, "pool": pool,
        "evaluation_count": 100, "generation": 0, "token": object(),
        "dispatch_snapshot": {}, "normal_base_view": view,
        "live_m1_snapshot_key": (1, "SESSION-1", minute, receipt),
        "sor_m1_calculation_snapshot": (None, None, {
            "forming_snapshot_source": "BROKER_AL_TR_FORMING_M1",
            "forming_snapshot_received_at": receipt,
        }),
    }
    emitted = []
    with patch.object(flow, "_normal_base_history_view", return_value=(view, 0)), patch.object(
        flow, "_sor_m1_calculation_view", return_value=view,
    ), patch.object(flow_module, "_build_validation_projection_data", return_value=(None, {})), patch.object(
        flow_module, "Thread", side_effect=lambda *, target, **_kwargs: SimpleNamespace(start=target),
    ), patch.object(flow, "_emit_signal_scan_completed", side_effect=emitted.append), patch(
        "routines.지표추종매매.routine.evaluate_signal_selection",
        return_value={"signal": "BUY"},
    ) as select:
        flow._start_normal_base_prepare(request, 5)
        assert select.call_count == 1
        assert emitted[-1]["live_sor_signal"] == {
            "signal": "BUY", "conflict": False, "stock_code": "005930",
            "timeframe_key": timeframe,
        }
        view["display_candles"] = [dict(formed, close=999)]
        request["token"] = object()
        flow._start_normal_base_prepare(request, 5)
    assert select.call_count == 1
    assert emitted[-1]["live_sor_signal"]["unavailable"] is True
    if timeframe in {"D1", "W1", "MO1", "Y1"}:
        view["display_candles"] = [formed]
        stale_minute = {"D1": "2026-10-21", "W1": "2026-10-26",
                        "MO1": "2026-11-02", "Y1": "2027-01-04"}[timeframe]
        request["live_m1_snapshot_key"] = (
            1, "SESSION-1", f"{stale_minute}T09:05:00+09:00", receipt,
        )
        request["token"] = object()
        with patch.object(flow, "_normal_base_history_view", return_value=(view, 0)), patch.object(
            flow, "_sor_m1_calculation_view", return_value=view,
        ), patch.object(flow_module, "_build_validation_projection_data", return_value=(None, {})), patch.object(
            flow_module, "Thread", side_effect=lambda *, target, **_kwargs: SimpleNamespace(start=target),
        ), patch.object(flow, "_emit_signal_scan_completed", side_effect=emitted.append):
            flow._start_normal_base_prepare(request, 5)
        assert emitted[-1]["live_sor_signal"]["unavailable"] is True


def test_live_status_publishes_only_after_current_normal_base_install():
    flow, window, pool, event = _observed_v5_case()
    key = id(window)
    session = flow._validation_sessions[key]
    snapshot_key = (1, "SESSION-1", event["forming_minute"], event["snapshot_received_at"])
    flow._sor_m1_last_snapshot[key] = snapshot_key
    flow._request_generation[key] = 0
    shown = []
    window.set_live_sor_monitor_signal = lambda result, *, source_as_of: shown.append(
        (result, source_as_of)
    )
    token = object()
    flow._prepare_active[key] = token
    payload = {
        "window_key": key, "generation": 0, "prepare_token": token,
        "session": session, "pool": pool, "evaluation_count": 100,
        "normal_base": True, "prepared_view_pool": pool,
        "normal_base_presentation": ((), None),
        "live_m1_snapshot_key": snapshot_key,
        "live_sor_signal": {"signal": "BUY", "stock_code": "005930",
                            "timeframe_key": "M1"},
    }
    with patch.object(flow, "_apply_pool_to_window", side_effect=lambda *_a, **_k: (
        shown.append("installed") or True
    )), patch.object(flow, "_start_pending_window_work"):
        flow._on_signal_scan_completed(payload)
        assert shown == ["installed", (payload["live_sor_signal"], snapshot_key[3])]
        shown.clear()
        new_token = object()
        flow._prepare_active[key] = new_token
        flow._sor_m1_observed_host._sor_forming_m1_by_stock["005930"][
            "candle"]["snapshot_received_at"] = "2026-10-20T09:05:12+09:00"
        flow._on_signal_scan_completed(dict(payload, prepare_token=new_token))
    assert shown == ["installed"]


def test_live_window_status_only_changes_existing_labels_in_normal_m1():
    from gui_indicator_follow_signal_validation_v5_window import (
        IndicatorFollowSignalValidationV5Window,
    )

    text_updates = []
    tooltip_updates = []
    stock = ValidationStockRef("005930", "Samsung")
    window = SimpleNamespace(
        stock=stock, _normal_base_ready=True, _normal_base_timeframe_key="M1",
        _replay_snapshot=None, validation_range=None,
        marking_display_check=SimpleNamespace(isChecked=lambda: False),
        validation_status_label=SimpleNamespace(setText=text_updates.append),
        result_summary_label=SimpleNamespace(setToolTip=tooltip_updates.append),
    )
    publish = IndicatorFollowSignalValidationV5Window.set_live_sor_monitor_signal
    result = {"stock_code": "005930", "timeframe_key": "M1", "signal": "SELL"}
    publish(window, result, source_as_of="2026-10-20T09:05:11+09:00")
    assert text_updates == ["SOR _AL M1 | SELL | 09:05:11 | read-only"]
    assert "no trade permission" in tooltip_updates[0]
    window._replay_snapshot = object()
    publish(window, result, source_as_of="2026-10-20T09:05:12+09:00")
    window._replay_snapshot = None
    publish(window, dict(result, stock_code="000660"),
            source_as_of="2026-10-20T09:05:12+09:00")
    assert len(text_updates) == len(tooltip_updates) == 1


@pytest.mark.parametrize("timeframe", ["M3", "M5", "M10", "M15", "M30", "M60", "M120", "M240",
                                       "D1", "W1", "MO1", "Y1"])
def test_live_window_status_requires_matching_selected_minute_timeframe(timeframe):
    from gui_indicator_follow_signal_validation_v5_window import (
        IndicatorFollowSignalValidationV5Window,
    )

    labels = []
    window = SimpleNamespace(
        stock=ValidationStockRef("005930", "Samsung"),
        _normal_base_ready=True, _normal_base_timeframe_key=timeframe,
        _replay_snapshot=None, validation_range=None,
        marking_display_check=SimpleNamespace(isChecked=lambda: False),
        validation_status_label=SimpleNamespace(setText=labels.append),
        result_summary_label=SimpleNamespace(setToolTip=lambda _text: None),
    )
    publish = IndicatorFollowSignalValidationV5Window.set_live_sor_monitor_signal
    result = {"stock_code": "005930", "timeframe_key": timeframe, "signal": "BUY"}
    for values, expected in ((result, "BUY"), (dict(result, signal="SELL"), "SELL"),
                             (dict(result, signal=None), "none"),
                             (dict(result, unavailable=True), "unavailable")):
        publish(window, values, source_as_of="2026-10-20T09:05:11+09:00")
        assert labels[-1] == f"SOR _AL {timeframe} | {expected} | 09:05:11 | read-only"
    publish(window, dict(result, timeframe_key="M1"),
            source_as_of="2026-10-20T09:05:12+09:00")
    assert len(labels) == 4


def _complete_morning_forming_host(minute=5):
    from gui_market_data_host import MarketDataHost
    from tests.test_display_only_integrated_history import (
        DisplayOnlyMarketDataHostTest, _opt10080_row,
    )
    from tests.test_sor_core_m1_input import NOW, _forming_host

    market = _forming_host()
    as_of = NOW.replace(minute=minute)
    rows = [
        _opt10080_row(f"2026102008{i:02d}00", 100 + i)
        for i in range(50)
    ] + [
        _opt10080_row(f"2026102009{i:02d}00", 200 + i)
        for i in range(minute + 1)
    ]
    with patch("gui_market_data_host.load_stock_library_snapshot",
               return_value=DisplayOnlyMarketDataHostTest._snapshot(True)), patch.object(
                   MarketDataHost, "direct_timeframe_requirements", return_value={1: 700}
               ):
        installed = MarketDataHost.install_display_timeframe_candles(
            market, "005930", 1, rows, as_of=as_of,
            market_data_identity="005930_AL", market_source="INTEGRATED",
            display_only=True, connection_epoch=1, login_session_id="SESSION-1",
            request_as_of=(as_of + timedelta(seconds=10)).isoformat(),
            received_at=(as_of + timedelta(seconds=11)).isoformat(),
        )
    assert installed["ok"] is True
    market._sor_m1_required_by_stock = {"005930": 5}
    market.project_broker_sor_m1_calculation = lambda **kwargs: (
        MarketDataHost.project_broker_sor_m1_calculation(market, **kwargs)
    )
    return market, as_of


@pytest.mark.parametrize("timeframe,minutes,target", [
    ("M3", 3, "20261020090300"), ("M5", 5, "20261020090500"),
    ("M10", 10, "20261020090000"), ("M15", 15, "20261020090000"),
    ("M30", 30, "20261020090000"), ("M60", 60, "20261020090000"),
    ("M120", 120, "20261020090000"), ("M240", 240, "20261020090000"),
])
@pytest.mark.parametrize("direct_target_present", [False, True])
def test_live_higher_minute_reuses_host_and_preserves_5000_direct_prior_bars(
    timeframe, minutes, target, direct_target_present,
):
    from copy import deepcopy
    flow, base_session, _pool, _market, _ = _registered_m1_calculation_case()
    market, as_of = _complete_morning_forming_host()
    flow.parent().existing_market_data_host = lambda: market
    flow._now_factory = lambda: as_of + timedelta(seconds=30)
    settings = SimpleNamespace(to_dict=lambda: {"bar": {"bar_minutes": minutes}})
    session = SimpleNamespace(request=SimpleNamespace(
        stock=base_session.request.stock, timeframe_key=timeframe,
        timeframe_minutes=minutes, settings_snapshot=settings,
    ))
    prior = [{
        "time": (datetime(2000, 1, 1) + timedelta(days=i)).strftime("%Y%m%d") + clock,
        "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1,
    } for i in range(2500 if minutes in (120, 240) else 5000)
        for clock in (("090000", "110000" if minutes == 120 else "130000")
                      if minutes in (120, 240) else ("090000",))]
    direct = [*prior]
    if direct_target_present:
        direct.append({"time": target, "open": 1, "high": 2,
                       "low": 1, "close": 1, "volume": 1})
    pool = {
        "stock": session.request.stock, "candles": direct,
        "market_source": "INTEGRATED", "market_data_identity": "005930_AL",
        "timeframe_key": timeframe, "timeframe_minutes": minutes,
        "requested_count": len(direct), "request_id": "direct-al",
    }
    original = deepcopy(pool)
    frozen = flow._sor_m1_calculation_snapshot(session, pool, live_forming=True)
    assert frozen[2]["available"] is True
    assert frozen[2]["forming_target_source_covered"] is True
    assert frozen[2]["today_m1_source_covered"] is True
    assert frozen[2]["projection"] == "FORMING_BASE_BAR"
    if direct_target_present:
        with pytest.raises(ValueError, match="SOR_FORMING_SOURCE_UNAVAILABLE"):
            flow._sor_m1_calculation_view(
                session, pool, dict(pool, display_candles=list(direct)), frozen,
            )
        assert pool == original
        return
    view = flow._sor_m1_calculation_view(
        session, pool, dict(pool, display_candles=list(direct)), frozen,
    )
    formed = frozen[2]["candles"][-1]
    expected = {"time": target, **{
        field: formed[field] for field in ("open", "high", "low", "close", "volume")
    }}
    assert view["candles"][:5000] == prior
    assert len(view["candles"]) == 5000 + len(frozen[2]["candles"])
    assert view["candles"][-1] == expected
    assert view["display_candles"][-1] == expected
    assert len(view["display_candles"]) == 5000
    assert view["snapshot"].to_rows() == flow._historical_rows_from_candles(view["candles"])
    assert view["snapshot"].request_as_of == frozen[2]["forming_snapshot_received_at"]
    assert pool == original


@pytest.mark.parametrize("today_direct_stamps", [
    [], ["20261020090000", "20261020090500"],
])
def test_live_higher_minute_keeps_direct_today_and_forms_only_current_target(today_direct_stamps):
    from copy import deepcopy

    flow, base_session, _pool, _market, _ = _registered_m1_calculation_case()
    market, as_of = _complete_morning_forming_host(minute=11)
    flow.parent().existing_market_data_host = lambda: market
    flow._now_factory = lambda: as_of + timedelta(seconds=30)
    session = SimpleNamespace(request=SimpleNamespace(
        stock=base_session.request.stock, timeframe_key="M5",
        timeframe_minutes=5,
        settings_snapshot=SimpleNamespace(to_dict=lambda: {"bar": {"bar_minutes": 5}}),
    ))
    prior = [
        {"time": f"20261019090{i}00", "open": 10, "high": 11,
         "low": 9, "close": 10, "volume": 1}
        for i in (0, 5)
    ]
    direct = prior + [
        {"time": stamp, "open": 1, "high": 2,
         "low": 1, "close": 1, "volume": 1}
        for stamp in today_direct_stamps
    ]
    pool = {
        "stock": session.request.stock, "candles": direct,
        "market_source": "INTEGRATED", "market_data_identity": "005930_AL",
        "timeframe_key": "M5", "timeframe_minutes": 5,
        "requested_count": len(direct), "request_id": "direct-al",
    }
    original = deepcopy(pool)
    frozen = flow._sor_m1_calculation_snapshot(session, pool, live_forming=True)
    assert frozen[2]["available"] is True
    assert frozen[2]["today_m1_source_covered"] is True
    projected_today = [
        {"time": row["bar_time"][:10].replace("-", "") +
         row["bar_time"][11:19].replace(":", ""),
         **{field: row[field] for field in ("open", "high", "low", "close", "volume")}}
        for row in frozen[2]["candles"]
        if row["bar_time"].startswith("2026-10-20")
    ]
    assert [row["time"] for row in projected_today] == ["20261020091000"]
    view = flow._sor_m1_calculation_view(
        session, pool, dict(pool, display_candles=list(direct)), frozen,
    )
    assert view["candles"] == direct + projected_today
    assert view["display_candles"] == direct + projected_today
    assert view["snapshot"].to_rows() == flow._historical_rows_from_candles(view["candles"])
    assert pool == original


def test_live_higher_minute_blocks_morning_m1_gap_before_complete_target():
    flow, base_session, _pool, _market, _ = _registered_m1_calculation_case()
    market, as_of = _complete_morning_forming_host(minute=11)
    flow.parent().existing_market_data_host = lambda: market
    flow._now_factory = lambda: as_of + timedelta(seconds=30)
    session = SimpleNamespace(request=SimpleNamespace(
        stock=base_session.request.stock, timeframe_key="M5",
        timeframe_minutes=5,
        settings_snapshot=SimpleNamespace(to_dict=lambda: {"bar": {"bar_minutes": 5}}),
    ))
    row = {"time": "20261019090000", "open": 10, "high": 11,
           "low": 9, "close": 10, "volume": 1}
    pool = dict(stock=session.request.stock, candles=[row], market_source="INTEGRATED",
                market_data_identity="005930_AL", timeframe_key="M5",
                timeframe_minutes=5, request_id="direct-al")
    market._display_timeframe_candles[("005930", 1)] = tuple(
        candle for candle in market._display_timeframe_candles[("005930", 1)]
        if not candle["bar_time"].startswith("2026-10-20T08:00:")
    )
    frozen = flow._sor_m1_calculation_snapshot(session, pool, live_forming=True)
    assert frozen[2]["available"] is True
    assert frozen[2]["forming_target_source_covered"] is True
    assert frozen[2]["today_m1_source_covered"] is False
    with pytest.raises(ValueError, match="TODAY_M1_COVERAGE_INCOMPLETE"):
        flow._sor_m1_calculation_view(session, pool, pool, frozen)


def test_live_higher_minute_blocks_missing_open_bucket_source_and_broker_anchor():
    flow, base_session, _pool, _market, _ = _registered_m1_calculation_case()
    market, as_of = _complete_morning_forming_host()
    flow.parent().existing_market_data_host = lambda: market
    flow._now_factory = lambda: as_of + timedelta(seconds=30)
    session = SimpleNamespace(request=SimpleNamespace(
        stock=base_session.request.stock, timeframe_key="M120",
        timeframe_minutes=120,
        settings_snapshot=SimpleNamespace(to_dict=lambda: {"bar": {"bar_minutes": 120}}),
    ))
    row = {"time": "20261019090000", "open": 10, "high": 11,
           "low": 9, "close": 10, "volume": 1}
    second = dict(row, time="20261019110000")
    pool = dict(stock=session.request.stock, candles=[row, second], market_source="INTEGRATED",
                market_data_identity="005930_AL", timeframe_key="M120",
                timeframe_minutes=120, request_id="direct-al")
    frozen = flow._sor_m1_calculation_snapshot(session, pool, live_forming=True)
    assert frozen[2]["forming_target_source_covered"] is True
    conflict = dict(pool, candles=[dict(row, time="20261019100000"), second])
    with pytest.raises(ValueError, match="SOURCE_CHANGED_AFTER_SNAPSHOT"):
        flow._sor_m1_calculation_view(session, conflict, conflict, frozen)
    with pytest.raises(ValueError, match="BROKER_TARGET_ANCHOR_UNVERIFIED"):
        flow._sor_m1_calculation_view(
            session, conflict, conflict,
            flow._sor_m1_calculation_snapshot(session, conflict, live_forming=True),
        )
    market._display_timeframe_candles[("005930", 1)] = tuple(
        candle for candle in market._display_timeframe_candles[("005930", 1)]
        if not candle["bar_time"].startswith("2026-10-20T09:00:")
    )
    missing = flow._sor_m1_calculation_snapshot(session, pool, live_forming=True)
    assert missing[2]["forming_target_source_covered"] is False
    with pytest.raises(ValueError, match="FORMING_SOURCE_INVALID"):
        flow._sor_m1_calculation_view(session, pool, pool, missing)


def test_higher_minute_afterhours_bucket_stays_unavailable_without_broker_anchor():
    flow, base_session, pool, market, _ = _registered_m1_calculation_case()
    session = SimpleNamespace(request=SimpleNamespace(
        stock=base_session.request.stock, timeframe_key="M3",
        timeframe_minutes=3,
        settings_snapshot=SimpleNamespace(to_dict=lambda: {"bar": {"bar_minutes": 3}}),
    ))
    pool.update(timeframe_key="M3", timeframe_minutes=3)
    flow._now_factory = lambda: datetime(2026, 10, 20, 15, 40, 30,
                                         tzinfo=ZoneInfo("Asia/Seoul"))
    market.project_broker_sor_m1_calculation = lambda **_kwargs: {
        "available": True,
        "candles": [{"bar_time": "2026-10-20T15:40:00+09:00"}],
    }
    frozen = flow._sor_m1_calculation_snapshot(session, pool, live_forming=True)
    assert frozen[2]["forming_target_source_covered"] is False


def _after_forming_case(timeframe, minutes, hour=15, minute=40):
    from gui_market_data_host import MarketDataHost
    from tests.test_display_only_integrated_history import (
        DisplayOnlyMarketDataHostTest, _opt10080_row,
    )
    from tests.test_sor_core_m1_input import _forming_host

    flow, base_session, _pool, _market, _ = _registered_m1_calculation_case()
    market = _forming_host()
    as_of = datetime(2026, 10, 20, hour, minute,
                     tzinfo=ZoneInfo("Asia/Seoul"))
    starts = ((8 * 60, 8 * 60 + 50), (9 * 60, 15 * 60 + 30),
              (15 * 60 + 40, hour * 60 + minute + 1))
    rows = [
        _opt10080_row(
            (as_of.replace(hour=0, minute=0) + timedelta(minutes=clock))
            .strftime("%Y%m%d%H%M%S"), 100 + clock,
        )
        for start, end in starts for clock in range(start, end)
    ]
    with patch("gui_market_data_host.load_stock_library_snapshot",
               return_value=DisplayOnlyMarketDataHostTest._snapshot(True)), patch.object(
                   MarketDataHost, "direct_timeframe_requirements", return_value={1: 700}
               ):
        installed = MarketDataHost.install_display_timeframe_candles(
            market, "005930", 1, rows, as_of=as_of,
            market_data_identity="005930_AL", market_source="INTEGRATED",
            display_only=True, connection_epoch=1, login_session_id="SESSION-1",
            request_as_of=(as_of + timedelta(seconds=10)).isoformat(),
            received_at=(as_of + timedelta(seconds=11)).isoformat(),
        )
    assert installed["ok"] is True
    market._sor_m1_required_by_stock = {"005930": 5}
    market.project_broker_sor_m1_calculation = lambda **kwargs: (
        MarketDataHost.project_broker_sor_m1_calculation(market, **kwargs)
    )
    flow.parent().existing_market_data_host = lambda: market
    flow._now_factory = lambda: as_of + timedelta(seconds=30)
    session = SimpleNamespace(request=SimpleNamespace(
        stock=base_session.request.stock, timeframe_key=timeframe,
        timeframe_minutes=minutes,
        settings_snapshot=SimpleNamespace(
            to_dict=lambda: {"bar": {"bar_minutes": minutes}}),
    ))
    regular = ["090000"] + (["110000"] if minutes == 120 else
                             ["130000"] if minutes == 240 else [])
    direct = [
        {"time": f"20261019{clock}", "open": 10, "high": 11,
         "low": 9, "close": 10, "volume": 1}
        for clock in [*regular, "154000"]
    ]
    pool = dict(stock=session.request.stock, candles=direct,
                market_source="INTEGRATED", market_data_identity="005930_AL",
                timeframe_key=timeframe, timeframe_minutes=minutes,
                requested_count=len(direct), request_id="direct-al")
    return flow, session, pool, market


@pytest.mark.parametrize("case", ["off_session", "active_session", "stale_date",
                                   "invalid_al", "forming", "incomplete_bar",
                                   "display_mismatch"])
def test_completed_m3_direct_al_auction_gaps_only_pass_off_session(case):
    from copy import deepcopy

    flow, session, pool, market = _after_forming_case("M3", 3)
    key = ("005930", 1)
    m1 = [dict(row, bar_time=row["bar_time"].replace("2026-10-20", "2026-10-23"),
               trade_date="2026-10-23")
          for row in market._display_timeframe_candles[key]]
    for minute in range(15 * 60 + 40, 20 * 60):
        clock = f"{minute // 60:02d}:{minute % 60:02d}"
        m1.append(dict(m1[-1], bar_time=f"2026-10-23T{clock}:00+09:00",
                       session="NXT_AFTER"))
    market._display_timeframe_candles[key] = tuple(m1)
    pool["candles"] = [
        dict(pool["candles"][0], time=f"{day}{clock}")
        for day, clock in (("20261021", "153000"), ("20261021", "153300"),
                           ("20261021", "153900"), ("20261023", "090000"),
                           ("20261023", "153000"), ("20261023", "153300"),
                           ("20261023", "153900"), ("20261023", "195700"))
    ]
    pool["requested_count"] = len(pool["candles"])
    if case == "stale_date":
        for row in pool["candles"]:
            row["time"] = row["time"].replace("20261023", "20261022")
    original = deepcopy(pool)
    view_pool = dict(pool, display_candles=list(pool["candles"]))
    flow._now_factory = lambda: datetime(2026, 10, 24 if case != "active_session" else 23,
                                         5 if case != "active_session" else 19,
                                         25 if case != "active_session" else 59,
                                         tzinfo=ZoneInfo("Asia/Seoul"))
    frozen = flow._sor_m1_calculation_snapshot(session, pool)
    assert frozen[1].date().isoformat() == "2026-10-23"
    assert frozen[2]["projection"] == "COMPLETED_TIMEFRAME"
    assert frozen[2]["available"] is True
    assert {f"202610{'22' if case == 'stale_date' else '23'}{clock}"
            for clock in ("153000", "153300", "153900")}.issubset(
        {row["time"] for row in pool["candles"]}
    )
    assert not {"08:50", "15:31", "15:32", "15:33", "15:34", "15:39"} & {
        row["bar_time"][11:16] for row in m1
    }
    if case == "invalid_al":
        view_pool["market_data_identity"] = "005930"
    elif case == "stale_date":
        frozen[2]["available"] = False
    elif case == "forming":
        frozen[2]["projection"] = "FORMING_BASE_BAR"
    elif case == "incomplete_bar":
        view_pool["candles"] = deepcopy(view_pool["candles"])
        view_pool["candles"][-1]["is_complete"] = False
    elif case == "display_mismatch":
        view_pool["display_candles"] = view_pool["display_candles"][:-1]
    if case != "off_session":
        with pytest.raises(ValueError, match=(
            "TODAY_TARGET_COVERAGE_MISMATCH" if case == "active_session"
            else "SOR_M1_BLOCKED"
        )):
            flow._sor_m1_calculation_view(session, pool, view_pool, frozen)
        return
    view = flow._sor_m1_calculation_view(
        session, pool, view_pool, frozen, expected_calculation=pool["candles"][-3:],
    )
    assert view["candles"] == original["candles"]
    assert view["display_candles"] == original["candles"]
    assert pool == original


@pytest.mark.parametrize("timeframe,minutes", [
    ("M5", 5), ("M10", 10), ("M30", 30),
    ("M60", 60), ("M120", 120), ("M240", 240),
])
def test_after_first_bucket_uses_verified_prior_broker_anchor(timeframe, minutes):
    from copy import deepcopy

    flow, session, pool, _market = _after_forming_case(timeframe, minutes)
    before = deepcopy(pool)
    frozen = flow._sor_m1_calculation_snapshot(session, pool, live_forming=True)
    assert frozen[2]["available"] is True
    assert frozen[2]["today_m1_source_covered"] is True
    assert frozen[2]["forming_target_source_covered"] is True
    view = flow._sor_m1_calculation_view(session, pool, pool, frozen)
    assert view["candles"][:len(before["candles"])] == before["candles"]
    assert view["candles"][-1]["time"] == "20261020154000"
    assert view["display_candles"][-1] == view["candles"][-1]
    assert view["snapshot"].to_rows() == flow._historical_rows_from_candles(view["candles"])
    assert view["sor_m1_calculation_complete"] is True
    assert pool == before


@pytest.mark.parametrize("minute,target", [
    (40, "20261020153900"), (41, "20261020153900"),
    (42, "20261020154200"), (43, "20261020154200"),
])
def test_after_m3_uses_direct_broker_anchor_without_fabricated_m1(minute, target):
    from copy import deepcopy
    from gui_market_data_host import MarketDataHost

    flow, session, pool, market = _after_forming_case("M3", 3, minute=minute)
    pool["candles"][-1]["time"] = "20261019153900"
    before = deepcopy(pool)
    source = MarketDataHost._current_display_candles(market, "005930", 1)
    assert not any(candle_market_datetime(row).strftime("%H%M") == "1539"
                   for row in source)
    frozen = flow._sor_m1_calculation_snapshot(session, pool, live_forming=True)
    assert frozen[2]["available"] is True
    assert frozen[2]["today_m1_source_covered"] is True
    assert frozen[2]["forming_target_source_covered"] is True
    assert [stamp for stamp in frozen[2]["today_target_starts"]
            if stamp >= "20261020153900"] == (
        ["20261020153900"] if minute < 42
        else ["20261020153900", "20261020154200"]
    )
    after = [row for row in frozen[2]["candles"]
             if row["bar_time"] >= "2026-10-20T15:39"]
    assert [row["bar_time"][11:16] for row in after] == (
        ["15:39"] if minute < 42 else ["15:39", "15:42"]
    )
    if minute >= 42:
        assert after[0]["is_complete"] is True
        assert after[0]["volume"] == sum(
            row["volume"] for row in source
            if candle_market_datetime(row).strftime("%H%M") in {"1540", "1541"}
        )
    view = flow._sor_m1_calculation_view(session, pool, pool, frozen)
    assert view["candles"][:len(before["candles"])] == before["candles"]
    assert view["candles"][-1]["time"] == target
    assert view["display_candles"] == view["candles"][-len(view["display_candles"]):]
    assert view["snapshot"].to_rows() == flow._historical_rows_from_candles(view["candles"])
    assert pool == before


@pytest.mark.parametrize("prior_after", [[], ["154000"], ["153900", "154300"]])
def test_after_m3_requires_aligned_selected_direct_broker_evidence(prior_after):
    flow, session, pool, _market = _after_forming_case("M3", 3)
    pool["candles"] = pool["candles"][:-1] + [
        dict(pool["candles"][-1], time=f"20261019{clock}")
        for clock in prior_after
    ]
    frozen = flow._sor_m1_calculation_snapshot(session, pool, live_forming=True)
    with pytest.raises(ValueError, match="BROKER_AFTER_ANCHOR_UNVERIFIED"):
        flow._sor_m1_calculation_view(session, pool, pool, frozen)


def test_after_m3_anchor_proof_rejects_wrong_source_and_prior_day_conflict():
    flow, session, pool, _market = _after_forming_case("M3", 3)
    pool["candles"][-1]["time"] = "20261019153900"
    as_of = flow._now_factory()
    assert flow._sor_m3_after_anchor_verified(pool, as_of)
    assert not flow._sor_m3_after_anchor_verified(
        dict(pool, market_data_identity="005930"), as_of,
    )
    assert not flow._sor_m3_after_anchor_verified(
        dict(pool, candles=[
            dict(pool["candles"][0], time="20261016090000"),
            dict(pool["candles"][-1], time="20261016153900"),
            *pool["candles"][:-1],
        ]), as_of,
    )
    assert not flow._sor_m3_after_anchor_verified(
        dict(pool, candles=[dict(pool["candles"][0], time="20261016154000"),
                            *pool["candles"]]), as_of,
    )


@pytest.mark.parametrize("timeframe,minutes,anchor", [
    ("M3", 3, "153900"), ("M5", 5, "154000"),
])
def test_after_keeps_all_5000_direct_prior_bars_and_visible_limit(timeframe, minutes, anchor):
    flow, session, pool, _market = _after_forming_case(timeframe, minutes)
    pool["candles"][-1]["time"] = f"20261019{anchor}"
    older = [
        {"time": (datetime(2000, 1, 1) + timedelta(days=i)).strftime("%Y%m%d")
         + "090000", "open": 10, "high": 11, "low": 9, "close": 10,
         "volume": 1}
        for i in range(4998)
    ]
    pool["candles"] = older + pool["candles"]
    direct = list(pool["candles"])
    frozen = flow._sor_m1_calculation_snapshot(session, pool, live_forming=True)
    view = flow._sor_m1_calculation_view(session, pool, pool, frozen)
    assert len(direct) == 5000
    assert view["candles"][:5000] == direct
    assert len(view["display_candles"]) == 5000
    assert view["display_candles"][-1]["time"] == f"20261020{anchor}"
    assert pool["candles"] == direct


@pytest.mark.parametrize("timeframe,minutes,hour,minute,target", [
    ("M5", 5, 15, 47, "20261020154500"),
    ("M10", 10, 15, 52, "20261020155000"),
    ("M30", 30, 16, 12, "20261020161000"),
    ("M60", 60, 16, 45, "20261020164000"),
    ("M120", 120, 17, 45, "20261020174000"),
    ("M240", 240, 19, 45, "20261020194000"),
])
def test_after_later_bucket_keeps_common_session_origin(
    timeframe, minutes, hour, minute, target,
):
    flow, session, pool, _market = _after_forming_case(
        timeframe, minutes, hour, minute,
    )
    pool["candles"].append(dict(
        pool["candles"][-1],
        time=(datetime(2026, 10, 19, 15, 40) + timedelta(minutes=minutes))
        .strftime("%Y%m%d%H%M%S"),
    ))
    frozen = flow._sor_m1_calculation_snapshot(session, pool, live_forming=True)
    assert frozen[2]["today_m1_source_covered"] is True
    assert frozen[2]["forming_target_source_covered"] is True
    view = flow._sor_m1_calculation_view(session, pool, pool, frozen)
    assert view["candles"][-1]["time"] == target
    assert view["display_candles"][-1]["time"] == target


@pytest.mark.parametrize("prior_after", [[], ["154500"], ["154000", "154500"]])
def test_after_rejects_missing_or_conflicting_prior_anchor(prior_after):
    flow, session, pool, _market = _after_forming_case("M10", 10)
    pool["candles"] = [row for row in pool["candles"]
                       if row["time"][8:] < "153000"] + [
        dict(pool["candles"][-1], time=f"20261019{clock}")
        for clock in prior_after
    ]
    frozen = flow._sor_m1_calculation_snapshot(session, pool, live_forming=True)
    assert frozen[2]["forming_target_source_covered"] is True
    with pytest.raises(ValueError, match="BROKER_AFTER_ANCHOR_UNVERIFIED"):
        flow._sor_m1_calculation_view(session, pool, pool, frozen)


def test_after_rejects_conflicting_older_direct_day():
    flow, session, pool, _market = _after_forming_case("M10", 10)
    pool["candles"].insert(0, dict(pool["candles"][-1], time="20261016154500"))
    frozen = flow._sor_m1_calculation_snapshot(session, pool, live_forming=True)
    with pytest.raises(ValueError, match="BROKER_AFTER_ANCHOR_UNVERIFIED"):
        flow._sor_m1_calculation_view(session, pool, pool, frozen)


def test_after_m15_remains_closed_without_broker_bucket_proof():
    flow, session, pool, _market = _after_forming_case("M15", 15)
    pool["candles"][-1]["time"] = "20261019154500"
    frozen = flow._sor_m1_calculation_snapshot(session, pool, live_forming=True)
    assert frozen[2]["forming_target_source_covered"] is False
    with pytest.raises(ValueError, match="BROKER_AFTER_ANCHOR_UNVERIFIED"):
        flow._sor_m1_calculation_view(session, pool, pool, frozen)


@pytest.mark.parametrize("minute,target", [
    (40, "20261020153000"), (44, "20261020153000"),
    (45, "20261020154500"),
])
def test_after_m15_uses_proven_partial_broker_bucket_without_fabricated_m1(minute, target):
    from copy import deepcopy
    from gui_market_data_host import MarketDataHost

    flow, session, pool, market = _after_forming_case("M15", 15, minute=minute)
    pool["candles"][-1]["time"] = "20261019153000"
    pool["candles"].append(dict(pool["candles"][-1], time="20261019154500"))
    before = deepcopy(pool)
    source = MarketDataHost._current_display_candles(market, "005930", 1)
    assert not any(candle_market_datetime(row).strftime("%H%M") == "1530"
                   for row in source)
    after_m1 = [row for row in source if candle_market_datetime(row).date()
                == datetime(2026, 10, 20).date()
                and candle_market_datetime(row).strftime("%H%M") >= "1540"]
    assert [candle_market_datetime(row).strftime("%H%M") for row in after_m1] == [
        f"15{clock:02d}" for clock in range(40, minute)
    ]
    assert market._sor_forming_m1_by_stock["005930"]["candle"][
        "bar_time"] == f"2026-10-20T15:{minute:02d}:00+09:00"
    frozen = flow._sor_m1_calculation_snapshot(session, pool, live_forming=True)
    assert frozen[2]["today_m1_source_covered"] is True
    assert frozen[2]["forming_target_source_covered"] is True
    assert [stamp for stamp in frozen[2]["today_target_starts"]
            if stamp >= "20261020153000"] == (
        ["20261020153000"] if minute < 45
        else ["20261020153000", "20261020154500"]
    )
    after = [row for row in frozen[2]["candles"]
             if row["bar_time"] >= "2026-10-20T15:30"]
    assert [row["bar_time"][11:16] for row in after] == (
        ["15:30"] if minute < 45 else ["15:30", "15:45"]
    )
    if minute == 45:
        first = after[0]
        physical = after_m1[:5]
        assert first["is_complete"] is True
        assert (first["open"], first["high"], first["low"], first["close"],
                first["volume"]) == (
            physical[0]["open"], max(row["high"] for row in physical),
            min(row["low"] for row in physical), physical[-1]["close"],
            sum(row["volume"] for row in physical),
        )
    view = flow._sor_m1_calculation_view(session, pool, pool, frozen)
    assert view["candles"][:len(before["candles"])] == before["candles"]
    assert view["candles"][-1]["time"] == target
    assert view["display_candles"] == view["candles"][-len(view["display_candles"]):]
    assert view["snapshot"].to_rows() == flow._historical_rows_from_candles(view["candles"])
    assert pool == before


@pytest.mark.parametrize("prior_after", [
    ["154500"], ["153000"], ["153000", "154000", "154500"],
    ["153000", "154500", "154500"],
])
def test_after_m15_rejects_missing_or_conflicting_direct_bucket_proof(prior_after):
    flow, session, pool, _market = _after_forming_case("M15", 15)
    pool["candles"] = pool["candles"][:-1] + [
        dict(pool["candles"][-1], time=f"20261019{clock}")
        for clock in prior_after
    ]
    frozen = flow._sor_m1_calculation_snapshot(session, pool, live_forming=True)
    assert frozen[2]["forming_target_source_covered"] is False
    with pytest.raises(ValueError, match="BROKER_AFTER_ANCHOR_UNVERIFIED"):
        flow._sor_m1_calculation_view(session, pool, pool, frozen)


def test_after_m15_rejects_conflicting_older_or_latest_direct_day():
    flow, session, pool, _market = _after_forming_case("M15", 15)
    pool["candles"][-1]["time"] = "20261019153000"
    pool["candles"].append(dict(pool["candles"][-1], time="20261019154500"))
    as_of = flow._now_factory()
    assert flow._sor_m15_after_anchor_verified(pool, as_of)
    conflict = dict(
        pool, candles=[dict(pool["candles"][0], time="20261016154000"),
                       *pool["candles"]])
    assert not flow._sor_m15_after_anchor_verified(conflict, as_of)
    frozen = flow._sor_m1_calculation_snapshot(session, conflict, live_forming=True)
    with pytest.raises(ValueError, match="BROKER_AFTER_ANCHOR_UNVERIFIED"):
        flow._sor_m1_calculation_view(session, conflict, conflict, frozen)
    current_normal = dict(pool, candles=[
        *pool["candles"], dict(pool["candles"][-1], time="20261020090000"),
    ])
    assert flow._sor_m15_after_anchor_verified(current_normal, as_of)
    assert not flow._sor_m15_after_anchor_verified(dict(
        pool, candles=[*pool["candles"],
                       dict(pool["candles"][-1], time="20261020160000")]), as_of)
    assert not flow._sor_m15_after_anchor_verified(dict(
        pool, candles=[pool["candles"][1], pool["candles"][0],
                       *pool["candles"][2:]]), as_of)
    assert not flow._sor_m15_after_anchor_verified(dict(
        pool, market_data_identity="005930"), as_of)
    for bad in ("20261020154600", "20261020xx0000", "20261019154500"):
        assert not flow._sor_m15_after_anchor_verified(dict(
            pool, candles=[*pool["candles"],
                           dict(pool["candles"][-1], time=bad)]), as_of)


def test_after_m15_rejects_missing_authenticated_forming_m1():
    flow, session, pool, market = _after_forming_case("M15", 15)
    pool["candles"][-1]["time"] = "20261019153000"
    pool["candles"].append(dict(pool["candles"][-1], time="20261019154500"))
    market._sor_forming_m1_by_stock.pop("005930")
    frozen = flow._sor_m1_calculation_snapshot(session, pool, live_forming=True)
    assert frozen[2]["available"] is False
    with pytest.raises(ValueError, match="SOR_FORMING_SOURCE_NOT_VERIFIED"):
        flow._sor_m1_calculation_view(session, pool, pool, frozen)


def test_after_m15_rejects_missing_completed_physical_m1():
    flow, session, pool, market = _after_forming_case("M15", 15, minute=45)
    pool["candles"][-1]["time"] = "20261019153000"
    pool["candles"].append(dict(pool["candles"][-1], time="20261019154500"))
    market._display_timeframe_candles[("005930", 1)] = tuple(
        row for row in market._display_timeframe_candles[("005930", 1)]
        if not row["bar_time"].endswith("15:44:00+09:00")
    )
    frozen = flow._sor_m1_calculation_snapshot(session, pool, live_forming=True)
    assert frozen[2]["available"] is False
    assert frozen[2]["availability_state"] == "SOR_M1_LAST_COMPLETED_MINUTE_MISSING"
    with pytest.raises(ValueError, match="SOR_M1_LAST_COMPLETED_MINUTE_MISSING"):
        flow._sor_m1_calculation_view(session, pool, pool, frozen)


@pytest.mark.parametrize("trace_env", [None, "0", "1"], ids=["unset", "off", "on"])
def test_sor_missing_last_minute_writes_one_bounded_diagnostic(tmp_path, monkeypatch, trace_env):
    import indicator_follow_signal_validation_runtime_trace as trace
    from gui_market_data_host import MarketDataHost

    if trace_env is None:
        monkeypatch.delenv("KIWOOM_SIGNAL_VALIDATION_TRACE", raising=False)
    else:
        monkeypatch.setenv("KIWOOM_SIGNAL_VALIDATION_TRACE", trace_env)
    monkeypatch.setattr(trace, "_TRACE_PATH", tmp_path / "trace.jsonl")
    if trace_env != "1":
        trace.trace_signal_validation_event("ordinary_trace_must_stay_off")
    flow, session, pool, market = _after_forming_case("M15", 15, minute=45)
    market._display_timeframe_candles[("005930", 1)] = tuple(
        row for row in market._display_timeframe_candles[("005930", 1)]
        if not row["bar_time"].endswith("15:44:00+09:00")
    )
    market._v5_al_native_minutes = {"005930": {
        "closed": {"minute": "2026-10-20T15:43:00+09:00"},
        "current": {"minute": "2026-10-20T15:45:00+09:00"},
    }}
    market._display_timeframe_candles[("005930", 3)] = (
        {"bar_time": "2026-10-19T15:30:00+09:00"},
    )
    market._display_timeframe_session_identity[("005930", 3)] = (1, "SESSION-1")
    for _ in range(2):
        result = MarketDataHost.project_broker_sor_m1_calculation(
            market, stock_code="005930", rules={"bar": {"bar_minutes": 15}},
            projection_request={"projection": "FORMING_BASE_BAR"},
            as_of=flow._now_factory(),
        )
        assert result["availability_state"] == "SOR_M1_LAST_COMPLETED_MINUTE_MISSING"
    assert trace._TRACE_PATH.exists(), "SOR block diagnostic must be recorded with trace OFF"
    assert trace.signal_validation_trace_enabled() is (trace_env == "1")
    records = [json.loads(line) for line in trace._TRACE_PATH.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    record = records[0]
    assert record["reason"] == "SOR_M1_LAST_COMPLETED_MINUTE_MISSING"
    assert record["stock"] == "005930"
    assert record["expected_latest"] == "2026-10-20T15:44:00+09:00"
    assert record["native_m1"]["current"] == "2026-10-20T15:45:00+09:00"
    assert record["native_m1"]["previous"] == "2026-10-20T15:43:00+09:00"
    assert record["native_m1"]["count"] == 2
    assert record["direct_al_m3"]["last"] == "2026-10-19T15:30:00+09:00"
    assert len(record["direct_al_m3"]["session_id_sha256"]) == 64
    assert record["source"]["count"] > 0
    assert record["branch"] == "LAST_COMPLETED_MINUTE"
    assert "SESSION-1" not in json.dumps(record)


@pytest.mark.parametrize("trace_env", [None, "0", "1"], ids=["unset", "off", "on"])
@pytest.mark.parametrize("branch", ["SOURCE_TARGET", "PROJECTED_TARGET"])
def test_sor_today_target_mismatch_writes_one_bounded_diagnostic(
    branch, tmp_path, monkeypatch, trace_env,
):
    import indicator_follow_signal_validation_runtime_trace as trace

    if trace_env is None:
        monkeypatch.delenv("KIWOOM_SIGNAL_VALIDATION_TRACE", raising=False)
    else:
        monkeypatch.setenv("KIWOOM_SIGNAL_VALIDATION_TRACE", trace_env)
    monkeypatch.setattr(trace, "_TRACE_PATH", tmp_path / "trace.jsonl")
    if branch == "SOURCE_TARGET":
        flow, session, pool, market, _ = _registered_m1_calculation_case()
        pool["candles"].append(dict(pool["candles"][-1], time="20261020090500"))
        frozen = flow._sor_m1_calculation_snapshot(session, pool)
    else:
        flow, session, pool, market = _after_forming_case("M5", 5)
        frozen = flow._sor_m1_calculation_snapshot(session, pool, live_forming=True)
        frozen[2]["today_target_starts"] = frozen[2]["today_target_starts"][:-1]
    for _ in range(2):
        with pytest.raises(ValueError, match="TODAY_TARGET_COVERAGE_MISMATCH"):
            flow._sor_m1_calculation_view(session, pool, pool, frozen)
    if trace_env != "1":
        trace.trace_signal_validation_event("ordinary_trace_must_stay_off")
    assert trace._TRACE_PATH.exists(), "SOR block diagnostic must be recorded with trace OFF"
    assert trace.signal_validation_trace_enabled() is (trace_env == "1")
    records = [json.loads(line) for line in trace._TRACE_PATH.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    record = records[0]
    assert record["reason"] == "TODAY_TARGET_COVERAGE_MISMATCH"
    assert record["branch"] == branch
    assert record["stock"] == "005930"
    assert record["source"]["count"] == len(pool["candles"])
    assert record["candidate"]["count"] == len(frozen[2]["candles"])
    assert record["difference"]["count"] > 0
    assert len(record["difference"]["sample"]) <= 8
    assert "SESSION-1" not in json.dumps(record)


def test_completed_m3_keeps_verified_al_history_before_short_m1_projection():
    from copy import deepcopy

    flow, session, pool, _market = _after_forming_case("M3", 3)
    frozen = flow._sor_m1_calculation_snapshot(session, pool)
    assert frozen[2]["available"] is True
    projected_today = [row for row in frozen[2]["candles"]
                       if candle_market_datetime(row).strftime("%Y%m%d") == "20261020"]
    assert len(projected_today) > 5
    direct_today = [{"time": candle_market_datetime(row).strftime("%Y%m%d%H%M%S"),
                     **{field: row[field] for field in
                        ("open", "high", "low", "close", "volume")}}
                    for row in projected_today]
    pool["candles"].extend(direct_today)
    pool["requested_count"] = len(pool["candles"])
    original = deepcopy(pool)
    frozen = flow._sor_m1_calculation_snapshot(session, pool)
    frozen[2]["candles"] = frozen[2]["candles"][-5:]

    view = flow._sor_m1_calculation_view(session, pool, pool, frozen)

    assert view["candles"] == original["candles"]
    assert pool == original


def test_completed_m3_rejects_gap_inside_short_m1_projection():
    flow, session, pool, _market = _after_forming_case("M3", 3)
    frozen = flow._sor_m1_calculation_snapshot(session, pool)
    projected_today = [row for row in frozen[2]["candles"]
                       if candle_market_datetime(row).strftime("%Y%m%d") == "20261020"]
    pool["candles"].extend(
        {"time": candle_market_datetime(row).strftime("%Y%m%d%H%M%S"),
         **{field: row[field] for field in ("open", "high", "low", "close", "volume")}}
        for row in projected_today[-5:]
    )
    frozen = flow._sor_m1_calculation_snapshot(session, pool)
    frozen[2]["candles"] = frozen[2]["candles"][-5:]
    del frozen[2]["candles"][-2]

    with pytest.raises(ValueError, match="TODAY_TARGET_COVERAGE_MISMATCH"):
        flow._sor_m1_calculation_view(session, pool, pool, frozen)


@pytest.mark.parametrize("diagnostic_failure", ["helper", "writer"])
def test_sor_diagnostic_failure_keeps_original_blocks(monkeypatch, tmp_path, diagnostic_failure):
    from gui_market_data_host import MarketDataHost
    import gui_market_data_host as market_module
    import indicator_follow_signal_validation_runtime_trace as trace

    def fail_trace(*_args, **_kwargs):
        raise OSError("diagnostic unavailable")

    monkeypatch.setenv("KIWOOM_SIGNAL_VALIDATION_TRACE", "0")
    if diagnostic_failure == "helper":
        monkeypatch.setattr(market_module, "_trace_sor_block", fail_trace)
    else:
        monkeypatch.setattr(trace, "_TRACE_PATH", tmp_path)  # Opening a directory fails.
    flow, session, pool, market = _after_forming_case("M15", 15, minute=45)
    market._display_timeframe_candles[("005930", 1)] = tuple(
        row for row in market._display_timeframe_candles[("005930", 1)]
        if not row["bar_time"].endswith("15:44:00+09:00")
    )
    result = MarketDataHost.project_broker_sor_m1_calculation(
        market, stock_code="005930", rules={"bar": {"bar_minutes": 15}},
        projection_request={"projection": "FORMING_BASE_BAR"},
        as_of=flow._now_factory(),
    )
    assert result["availability_state"] == "SOR_M1_LAST_COMPLETED_MINUTE_MISSING"

    flow, session, pool, _market, _ = _registered_m1_calculation_case()
    pool["candles"].append(dict(pool["candles"][-1], time="20261020090500"))
    frozen = flow._sor_m1_calculation_snapshot(session, pool)
    with pytest.raises(ValueError, match="TODAY_TARGET_COVERAGE_MISMATCH"):
        flow._sor_m1_calculation_view(session, pool, pool, frozen)


def test_after_m15_keeps_all_5000_direct_prior_bars_and_visible_limit():
    flow, session, pool, _market = _after_forming_case("M15", 15)
    pool["candles"][-1]["time"] = "20261019153000"
    pool["candles"].append(dict(pool["candles"][-1], time="20261019154500"))
    pool["candles"] = [
        {"time": (datetime(2000, 1, 1) + timedelta(days=i)).strftime("%Y%m%d")
         + "090000", "open": 10, "high": 11, "low": 9, "close": 10,
         "volume": 1}
        for i in range(4997)
    ] + pool["candles"]
    direct = list(pool["candles"])
    frozen = flow._sor_m1_calculation_snapshot(session, pool, live_forming=True)
    view = flow._sor_m1_calculation_view(session, pool, pool, frozen)
    assert len(direct) == 5000
    assert view["candles"][:5000] == direct
    assert len(view["display_candles"]) == 5000
    assert view["display_candles"][-1]["time"] == "20261020153000"
    assert pool["candles"] == direct


@pytest.mark.parametrize("missing", ["080000", "090000"])
def test_after_requires_full_day_m1_source(missing):
    flow, session, pool, market = _after_forming_case("M5", 5)
    market._display_timeframe_candles[("005930", 1)] = tuple(
        row for row in market._display_timeframe_candles[("005930", 1)]
        if not row["bar_time"].endswith(f"{missing[:2]}:{missing[2:4]}:00+09:00")
    )
    frozen = flow._sor_m1_calculation_snapshot(session, pool, live_forming=True)
    assert frozen[2]["today_m1_source_covered"] is False
    with pytest.raises(ValueError, match="TODAY_M1_COVERAGE_INCOMPLETE"):
        flow._sor_m1_calculation_view(session, pool, pool, frozen)


def test_after_rejects_missing_authenticated_current_m1():
    flow, session, pool, market = _after_forming_case("M5", 5)
    market._sor_forming_m1_by_stock.pop("005930")
    frozen = flow._sor_m1_calculation_snapshot(session, pool, live_forming=True)
    assert frozen[2]["available"] is False
    with pytest.raises(ValueError, match="SOR_FORMING_SOURCE_NOT_VERIFIED"):
        flow._sor_m1_calculation_view(session, pool, pool, frozen)


def test_registered_selected_al_m1_projection_is_detached_and_shared_by_scan_detail():
    from copy import deepcopy
    from routines.지표추종매매.routine_validation_replay import project_validation_candles

    flow, session, pool, market, original_cache = _registered_m1_calculation_case()
    raw = deepcopy(pool)
    frozen = flow._sor_m1_calculation_snapshot(session, pool)
    selected_view = flow._validation_pool_for_session(session, pool, target_count=5)
    view = flow._sor_m1_calculation_view(session, pool, selected_view, frozen)
    scan_candles, dropped = project_validation_candles(view["snapshot"])
    assert dropped == 0
    assert [row["close"] for row in scan_candles] == [101, 102, 103, 104, 105]
    assert view["candles"] == scan_candles
    assert view["display_candles"] == raw["candles"]
    assert view["calculation_source"] == "SELECTED_HISTORY_WITH_BROKER_AL_CONFIRMED_M1_TODAY"
    assert view["source_proof"] == "BROKER_ROWS_NOT_INDEPENDENT_MERGE_ATTESTATION"
    assert view["market_data_identity"] == "005930_AL"

    detail_inputs = []

    class CaptureReplay:
        def evaluate_with_context(self, snapshot, **kwargs):
            detail_inputs.append(project_validation_candles(snapshot)[0])
            raise RuntimeError("detail input captured")

    flow._replay_factory = lambda _session: CaptureReplay()
    with pytest.raises(RuntimeError, match="detail input captured"):
        flow._prepare_replay_result_from_pool(
            session, pool, history_target=5, evaluation_count=5,
            prepared_view_pool=view, sor_m1_calculation_snapshot=frozen,
        )
    assert detail_inputs == [scan_candles]
    assert pool == raw
    assert market._display_timeframe_candles == original_cache


def test_registered_al_m1_scan_and_detail_use_same_verified_calculation():
    from routines.지표추종매매.routine_validation_replay import project_validation_candles

    flow, session, pool, _market, _cache = _registered_m1_calculation_case()
    window = SimpleNamespace(_normal_base_requested=False)
    key = id(window)
    flow._history_targets[key] = 5
    request = {
        "window": window, "session": session, "pool": pool,
        "evaluation_count": 5, "generation": 0, "token": object(),
        "dispatch_snapshot": {},
    }
    seen = []
    emitted = []

    class Replay:
        def scan_signal_entries(self, _snapshot, **_kwargs):
            return ()

        def evaluate_with_context(self, snapshot, **_kwargs):
            seen.append([row["close"] for row in project_validation_candles(snapshot)[0]])
            raise RuntimeError("detail input captured")

    flow._replay_factory = lambda _session: Replay()
    with patch.object(flow, "_prepare_request_is_current", return_value=True), patch(
        "gui_indicator_follow_signal_validation_v5_flow.Thread",
        side_effect=lambda *, target, **_kwargs: SimpleNamespace(start=target),
    ), patch.object(flow, "_emit_signal_scan_completed", side_effect=emitted.append):
        flow._start_pool_prepare(request)

    assert emitted[0]["error"] == "detail input captured"
    assert seen == [[101, 102, 103, 104, 105]]


def test_registered_m1_handoff_preserves_prior_selected_history():
    flow, session, pool, _market, _ = _registered_m1_calculation_case()
    prior = {
        "time": "20261019090000", "open": 77, "high": 78,
        "low": 76, "close": 77, "volume": 9,
    }
    pool["candles"] = [prior, *pool["candles"]]
    pool["requested_count"] = 6
    frozen = flow._sor_m1_calculation_snapshot(session, pool)
    selected = flow._validation_pool_for_session(session, pool, target_count=6)
    view = flow._sor_m1_calculation_view(session, pool, selected, frozen)

    assert view["candles"][0] == prior
    assert [row["close"] for row in view["candles"][1:]] == [101, 102, 103, 104, 105]
    assert view["display_candles"] == pool["candles"]
    older_view = dict(selected, candles=[prior], display_candles=[prior])
    assert flow._sor_m1_calculation_view(session, pool, older_view, frozen) is older_view
    with pytest.raises(ValueError, match="CALCULATION_VIEW_UNALIGNED"):
        flow._sor_m1_calculation_view(
            session, pool, dict(selected, source_coverage_sufficient=False), frozen,
        )


@pytest.mark.parametrize("timeframe", ["W1", "MO1", "Y1"])
def test_period_prior_close_missing_remains_fail_closed(tmp_path, timeframe):
    flow = SimpleNamespace(_historical_cache=SimpleNamespace(root=tmp_path))
    with pytest.raises(ValueError, match="PERIOD_CUMULATIVE_SOURCE_UNAVAILABLE"):
        flow_module.IndicatorFollowSignalValidationV5Flow._sor_period_prior_close(
            flow, "005930", timeframe, "20261019000000",
            datetime(2026, 10, 20, tzinfo=ZoneInfo("Asia/Seoul")),
        )


def test_registered_d1_uses_only_covered_today_m1_and_preserves_direct_history():
    flow, session, pool, _market, _ = _registered_m1_calculation_case()
    as_of = datetime(2026, 10, 20, 8, 3, 30, tzinfo=ZoneInfo("Asia/Seoul"))
    session = SimpleNamespace(request=SimpleNamespace(
        stock=session.request.stock, timeframe_key="D1", timeframe_minutes=1,
    ))
    prior = {"time": "20261019000000", "open": 80, "high": 90,
             "low": 70, "close": 85, "volume": 50}
    current = {"time": "20261020000000", "open": 999, "high": 999,
               "low": 999, "close": 999, "volume": 999}
    pool.update(candles=[prior, current], timeframe_key="D1", request_id="direct-al-d1")
    rows = [
        {"bar_time": f"2026-10-20T08:0{i}:00+09:00", "open": 100 + i,
         "high": 105 + i, "low": 99 + i, "close": 101 + i,
         "volume": i + 1, "is_complete": i != 3}
        for i in range(4)
    ]
    projected = {
        "available": True, "projection": "FORMING_BASE_BAR",
        "complete": False, "forming_included": True,
        "completeness": {"forming_included": True},
        "forming_snapshot_source": "BROKER_AL_TR_FORMING_M1",
        "forming_snapshot_received_at": as_of.isoformat(),
        "calculation_only": True, "calculation_source": "BROKER_AL_CONFIRMED_M1",
        "source_proof": "BROKER_ROWS_NOT_INDEPENDENT_MERGE_ATTESTATION",
        "market_source": "INTEGRATED", "market_data_identity": "005930_AL",
        "timeframe_minutes": 1, "freshness": {"as_of": as_of.isoformat(timespec="seconds")},
        "candles": rows, "source_identity": "confirmed-m1",
    }
    frozen = (flow._pool_computation_source_identity(pool), as_of, projected)
    view = flow._sor_m1_calculation_view(session, pool, pool, frozen)

    assert view["candles"][0] == prior
    assert view["candles"][1] == {
        "time": current["time"], "open": 100, "high": 108,
        "low": 99, "close": 104, "volume": 10,
    }
    assert view["display_candles"] == pool["candles"]
    assert pool["candles"] == [prior, current]
    assert view["snapshot"].to_rows() == flow._historical_rows_from_candles(view["candles"])
    live = flow._sor_m1_calculation_view(session, pool, pool, frozen, live_calendar=True)
    assert live["display_candles"] == live["candles"]
    assert pool["candles"] == [prior, current]
    older = dict(pool, candles=[prior], display_candles=[prior])
    assert flow._sor_m1_calculation_view(session, pool, older, frozen) is older

    projected["candles"] = rows[1:]
    with pytest.raises(ValueError, match="D1_M1_COVERAGE_INCOMPLETE"):
        flow._sor_m1_calculation_view(session, pool, pool, frozen)


def test_registered_d1_requests_existing_host_forming_projection():
    flow, session, pool, market, _ = _registered_m1_calculation_case()
    session = SimpleNamespace(request=SimpleNamespace(
        stock=session.request.stock, timeframe_key="D1",
        timeframe_minutes=5, settings_snapshot=SimpleNamespace(
            to_dict=lambda: {"bar": {"bar_minutes": 5}}),
    ))
    pool["timeframe_key"] = "D1"
    calls = []
    market.project_broker_sor_m1_calculation = lambda **kwargs: calls.append(kwargs) or {}

    assert flow._sor_m1_calculation_snapshot(session, pool) is not None
    assert calls[0]["projection_request"] == {"projection": "FORMING_BASE_BAR"}
    assert calls[0]["rules"] == {"bar": {"bar_minutes": 1}}


@pytest.mark.parametrize("timeframe", ["W1", "MO1", "Y1"])
def test_registered_period_requests_existing_host_m1_forming_projection(timeframe):
    flow, session, pool, market, _ = _registered_m1_calculation_case()
    session = SimpleNamespace(request=SimpleNamespace(
        stock=session.request.stock, timeframe_key=timeframe,
        settings_snapshot=session.request.settings_snapshot,
    ))
    pool["timeframe_key"] = timeframe
    calls = []
    market.project_broker_sor_m1_calculation = lambda **kwargs: calls.append(kwargs) or {}

    assert flow._sor_m1_calculation_snapshot(session, pool) is not None
    assert calls[0]["rules"] == {"bar": {"bar_minutes": 1}}
    assert calls[0]["projection_request"] == {"projection": "FORMING_BASE_BAR"}


def test_registered_m1_handoff_skips_krx_and_unregistered_al():
    flow, session, pool, market, _ = _registered_m1_calculation_case()
    krx = dict(pool, market_source="KRX", market_data_identity="005930")
    assert flow._sor_m1_calculation_snapshot(session, krx) is None
    with pytest.raises(ValueError, match="VALIDATION_POOL_MARKET_IDENTITY_MISMATCH"):
        flow._validation_pool_for_session(session, krx, target_count=5)
    market._sor_m1_required_by_stock.clear()
    assert flow._sor_m1_calculation_snapshot(session, pool) is None
    selected = flow._validation_pool_for_session(session, pool, target_count=5)
    assert selected["candles"] == pool["candles"]
    with pytest.raises(ValueError, match="SOR_M1_BLOCKED:SOURCE_UNAVAILABLE"):
        flow._sor_m1_calculation_view(session, pool, selected, None)


@pytest.mark.parametrize("gap", ["target_time", "missing_m1"])
def test_registered_m1_handoff_blocks_unaligned_or_missing_completed_input(gap):
    flow, session, pool, market, _ = _registered_m1_calculation_case()
    if gap == "target_time":
        pool["candles"][-1] = dict(pool["candles"][-1], time="20261020090500")
    else:
        key = ("005930", 1)
        market._display_timeframe_candles[key] = tuple(
            row for row in market._display_timeframe_candles[key]
            if not row["bar_time"].startswith("2026-10-20T09:03:")
        )
    frozen = flow._sor_m1_calculation_snapshot(session, pool)
    view = flow._validation_pool_for_session(session, pool, target_count=5)
    with pytest.raises(ValueError, match="SOR_M1_BLOCKED"):
        flow._sor_m1_calculation_view(session, pool, view, frozen)


def test_range_handoff_rejects_changed_calculation_values():
    flow, session, pool, _market, _ = _registered_m1_calculation_case()
    frozen = flow._sor_m1_calculation_snapshot(session, pool)
    view = flow._validation_pool_for_session(session, pool, target_count=5)
    prepared = flow._sor_m1_calculation_view(session, pool, view, frozen)
    displayed = [dict(row) for row in prepared["candles"]]
    displayed[-1]["close"] += 1
    with pytest.raises(ValueError, match="DISPLAYED_CALCULATION_CHANGED"):
        flow._sor_m1_calculation_view(
            session, pool, view, frozen, displayed,
        )


@pytest.mark.parametrize("integrated", [False, True])
@pytest.mark.parametrize(
    ("timeframe", "interval", "trcode"),
    [
        ("M1", 1, "opt10080"), ("M3", 3, "opt10080"),
        ("M5", 5, "opt10080"), ("M10", 10, "opt10080"),
        ("M15", 15, "opt10080"), ("M30", 30, "opt10080"),
        ("M60", 60, "opt10080"), ("M120", 120, "opt10080"),
        ("M240", 240, "opt10080"), ("D1", None, "opt10081"),
        ("W1", None, "opt10082"), ("MO1", None, "opt10083"),
        ("Y1", None, "opt10094"),
    ],
)
def test_selected_timeframe_routes_once_and_checks_broker_provenance(
    timeframe, interval, trcode, integrated
):
    calls = []
    response_override = {}
    expected_method = (
        ("request_minute_candles_display_only_integrated" if integrated else
         "request_minute_candles_read_only") if interval is not None else
        ("request_period_candles_read_only_integrated" if integrated else
         "request_period_candles_read_only")
    )
    identity = "005930_AL" if integrated else "005930"
    source = "INTEGRATED" if integrated else "KRX"

    def broker_request(method_name):
        def request(code, name, **kwargs):
            calls.append((method_name, code, name, dict(kwargs)))
            response = {
                "ok": True, "type": "minute_candles" if interval is not None else "period_candles",
                "code": code, "request_id": "req-1", "rqname": "req-1",
                "market_data_identity": identity, "market_source": source,
                "rows": [], "rows_count": 0, "trcode": trcode,
            }
            response["interval" if interval is not None else "timeframe_key"] = timeframe if interval is None else interval
            response.update(response_override)
            kwargs["callback"](response)
            return {"ok": True, "rqname": "req-1"}
        return request

    broker = SimpleNamespace(**{
        method_name: broker_request(method_name) for method_name in (
            "request_minute_candles_display_only_integrated",
            "request_minute_candles_read_only",
            "request_period_candles_read_only_integrated",
            "request_period_candles_read_only",
        )
    })
    if not integrated:
        with pytest.raises(ValueError, match="SOR_SOURCE_UNAVAILABLE"):
            flow_module._V5SelectedSourceRequester(
                broker, ValidationStockRef("005930", "Samsung"), timeframe, False,
            )
        assert calls == []
        return
    requester = flow_module._V5SelectedSourceRequester(
        broker, ValidationStockRef("005930", "Samsung"), timeframe, True
    )
    results = []
    if interval is None:
        request = lambda **kwargs: requester.request_period_candles_read_only(
            "005930", "Samsung", timeframe_key=timeframe, count=120,
            callback=results.append, **kwargs
        )
    else:
        request = lambda **kwargs: requester.request_minute_candles_read_only(
            "005930", "Samsung", interval=interval, count=120,
            callback=results.append, **kwargs
        )

    assert request() == {"ok": True, "rqname": "req-1"}
    assert len(calls) == 1
    method_name, code, name, kwargs = calls[0]
    assert (method_name, code, name) == (expected_method, "005930", "Samsung")
    assert {key: value for key, value in kwargs.items() if key != "callback"} == {
        "count": 120,
        "interval" if interval is not None else "timeframe_key":
            interval if interval is not None else timeframe,
        **({"nxt_available": True} if integrated else {}),
    }
    assert results[-1]["ok"] is True
    assert (results[-1]["type"], results[-1]["trcode"],
            results[-1]["market_data_identity"], results[-1]["market_source"],
            results[-1]["request_id"]) == (
                "minute_candles" if interval is not None else "period_candles",
                trcode, identity, source, "req-1")

    response_override["request_id"] = "wrong"
    request()
    assert len(calls) == 2
    assert results[-1] == {"ok": False, "error": "V5_BROKER_SOURCE_PROVENANCE_INVALID"}


@pytest.mark.parametrize("integrated", [False, True])
def test_mo1_rejects_wrong_request_identity_before_broker(integrated):
    calls = []
    broker = SimpleNamespace(
        request_period_candles_read_only=lambda *args, **kwargs: calls.append("KRX"),
        request_period_candles_read_only_integrated=lambda *args, **kwargs: calls.append("AL"),
        request_minute_candles_read_only=lambda *args, **kwargs: calls.append("minute"),
        request_minute_candles_display_only_integrated=lambda *args, **kwargs: calls.append("minute_AL"),
    )
    if not integrated:
        with pytest.raises(ValueError, match="SOR_SOURCE_UNAVAILABLE"):
            flow_module._V5SelectedSourceRequester(
                broker, ValidationStockRef("005930", "Samsung"), "MO1", False,
            )
        assert calls == []
        return
    requester = flow_module._V5SelectedSourceRequester(
        broker, ValidationStockRef("005930", "Samsung"), "MO1", True
    )
    results = []
    requester.request_period_candles_read_only(
        "005930", "Samsung", timeframe_key="Y1", count=120, callback=results.append
    )
    requester.request_minute_candles_read_only(
        "005930", "Samsung", interval=1, count=120, callback=results.append
    )
    assert calls == []
    assert results == [
        {"ok": False, "error": "V5_BROKER_REQUEST_IDENTITY_INVALID"},
        {"ok": False, "error": "V5_BROKER_REQUEST_IDENTITY_INVALID"},
    ]


def _response(source, kind, timeframe, request_id):
    return {
        "ok": True, "type": "minute_candles", "code": "005930",
        "interval": 5, "request_id": request_id, "rqname": request_id,
        "market_data_identity": source, "market_source": kind,
        "rows": [], "rows_count": 0, "timeframe_key": timeframe,
        "trcode": "opt10080",
    }


@pytest.mark.parametrize("invalid_field", ["request_id", "market_data_identity", "market_source", "code", "interval", "trcode"])
def test_invalid_integrated_response_is_rejected_without_krx_retry(invalid_field):
    calls = []

    class Broker:
        def request_minute_candles_display_only_integrated(self, code, name, **kwargs):
            calls.append("AL")
            response = _response("005930_AL", "INTEGRATED", "M5", "req-1")
            response[invalid_field] = "wrong"
            kwargs["callback"](response)
            return {"ok": True, "rqname": "req-1"}

        def request_minute_candles_read_only(self, *args, **kwargs):
            calls.append("KRX")

    requester = flow_module._V5SelectedSourceRequester(
        Broker(), ValidationStockRef("005930", "Samsung"), "M5", True
    )
    results = []
    requester.request_minute_candles_read_only(
        "005930", "Samsung", interval=5, count=5900, callback=results.append
    )
    assert calls == ["AL"]
    assert results == [{"ok": False, "error": "V5_BROKER_SOURCE_PROVENANCE_INVALID"}]


def test_broker_start_failure_cannot_validate_buffered_al_callback():
    class Broker:
        def request_minute_candles_display_only_integrated(self, code, name, **kwargs):
            kwargs["callback"](_response("005930_AL", "INTEGRATED", "M5", "req-1"))
            return {"ok": False, "rqname": "req-1", "error": "dispatch failed"}

    requester = flow_module._V5SelectedSourceRequester(
        Broker(), ValidationStockRef("005930", "Samsung"), "M5", True
    )
    results = []
    requester.request_minute_candles_read_only(
        "005930", "Samsung", interval=5, count=5900, callback=results.append
    )
    assert len(results) == 1 and results[0]["ok"] is False


@pytest.mark.parametrize("eligibility", [None, False, "True"])
def test_period_integrated_request_rejects_unverified_eligibility(eligibility):
    calls = []
    broker = SimpleNamespace(
        _finish_callback=lambda callback, payload: callback(payload),
        _request_period_candles_read_only_common=lambda *args, **kwargs: calls.append(kwargs),
    )
    results = []
    KiwoomApi.request_period_candles_read_only_integrated(
        broker, "005930", timeframe_key="D1", nxt_available=eligibility,
        callback=results.append,
    )
    assert calls == []
    assert results[0]["ok"] is False


def test_period_integrated_request_selects_al_common_path():
    calls = []
    broker = SimpleNamespace(
        _finish_callback=lambda callback, payload: callback(payload),
        _request_period_candles_read_only_common=lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    KiwoomApi.request_period_candles_read_only_integrated(
        broker, "005930", timeframe_key="D1", count=600, nxt_available=True,
    )
    assert calls[0][1]["integrated"] is True
    assert calls[0][1]["timeframe_key"] == "D1"


def test_integrated_period_request_dispatches_actual_al_input_without_commit():
    pending = {}
    broker = SimpleNamespace(
        VALIDATION_PERIOD_TR_CODES=KiwoomApi.VALIDATION_PERIOD_TR_CODES,
        _pending_tr=pending,
        is_available=lambda: True,
        is_connected=lambda: True,
        _capture_broker_request_identity=lambda: {"connection_epoch": 1},
        _claim_tr_screen=lambda **kwargs: ("9001", None),
        _submit_governed_tr_request=lambda **kwargs: {"ok": True, "status": "REQUESTED"},
        _finish_callback=lambda callback, payload: payload,
    )
    broker._request_period_candles_read_only_common = (
        lambda *args, **kwargs: KiwoomApi._request_period_candles_read_only_common(
            broker, *args, **kwargs
        )
    )
    started = KiwoomApi.request_period_candles_read_only_integrated(
        broker, "005930", timeframe_key="W1", count=300, nxt_available=True
    )
    assert started["ok"] is True
    request = pending[started["rqname"]]
    assert request["market_data_identity"] == "005930_AL"
    assert request["market_source"] == "INTEGRATED"
    assert request["inputs"][0][1] == "005930_AL"
    assert request["type"] == "period_candles"


def test_integrated_candle_and_signal_cache_is_separate_and_stock_delete_removes_both(tmp_path):
    cache = IndicatorFollowSignalValidationHistoricalCache(tmp_path)
    candles = [{
        "time": "20261019090500", "open": 100, "high": 105,
        "low": 99, "close": 103, "volume": 1000,
    }]
    for identity, source in (("005930", "KRX"), ("005930_AL", "INTEGRATED")):
        assert cache.store(
            stock_code="005930", stock_name="Samsung", timeframe_minutes=5,
            timeframe_key="M5", requested_count=1, candles=candles,
            market_data_identity=identity, market_source=source,
        )
        assert cache.store_signal_entries(
            stock_code="005930", timeframe_minutes=5, timeframe_key="M5",
            requested_count=1, settings_hash="hash", entries=[{"source": source}],
            market_data_identity=identity,
        )
    assert cache.path_for("005930", 5, 1, "M5") != cache.path_for(
        "005930", 5, 1, "M5", market_data_identity="005930_AL"
    )
    for identity, source in (("005930", "KRX"), ("005930_AL", "INTEGRATED")):
        entry = cache.load("005930", 5, 1, "M5", market_data_identity=identity)
        assert entry is not None and entry.market_source == source
        assert cache.load_signal_entries(
            "005930", 5, 1, "hash", "M5", market_data_identity=identity
        ) == [{"source": source}]
    deleted = cache.delete_stock("005930")
    assert deleted["ok"] is True
    assert not list(tmp_path.glob("005930*.json"))


def test_invalid_source_pair_cannot_overwrite_krx_cache(tmp_path):
    cache = IndicatorFollowSignalValidationHistoricalCache(tmp_path)
    kwargs = dict(
        stock_code="005930", stock_name="Samsung", timeframe_minutes=5,
        timeframe_key="M5", requested_count=1,
        candles=[{"time": "20261019090500", "open": 100, "high": 105,
                  "low": 99, "close": 103, "volume": 1000}],
    )
    assert cache.store(**kwargs, market_data_identity="005930", market_source="KRX")
    assert not cache.store(**kwargs, market_data_identity="005930", market_source="INTEGRATED")
    assert cache.load("005930", 5, 1, "M5").market_source == "KRX"


def test_integrated_cache_marker_rejects_changed_candle_content(tmp_path):
    from tests.test_indicator_follow_signal_validation_v5_integrated_history_scroll import _flow

    flow = _flow()
    cache = IndicatorFollowSignalValidationHistoricalCache(tmp_path)
    flow._historical_cache = cache
    stock = ValidationStockRef("005930", "Samsung")
    flow._validation_source_by_code[stock.code] = True
    candles = [{
        "time": "20261019090500", "open": 100, "high": 105,
        "low": 99, "close": 103, "volume": 1000,
    }]
    assert flow._persist_history(
        stock, 5, "M5", 1, candles,
        market_data_identity="005930_AL", market_source="INTEGRATED",
    )
    assert flow._cached_history(stock, 5, "M5", 1) is not None
    path = cache.path_for("005930", 5, 1, "M5", market_data_identity="005930_AL")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["candles"][0]["close"] = 104
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert flow._cached_history(stock, 5, "M5", 1) is None


def test_v5_identity_follows_verified_source_and_unknown_blocks():
    stock = ValidationStockRef("005930", "Samsung")
    flow_type = flow_module.IndicatorFollowSignalValidationV5Flow
    flow = SimpleNamespace(_validation_source_by_code={"005930": True})
    assert flow_type._validation_market_data_identity(flow, stock) == "005930_AL"
    with pytest.raises(ValueError, match="NXT_ELIGIBILITY_NOT_VERIFIED"):
        flow_type._validation_market_data_identity(
            SimpleNamespace(_validation_source_by_code={"005930": None}), stock
        )


def test_v5_legacy_krx_identity_cannot_select_historical_source():
    stock = ValidationStockRef("005930", "Samsung")
    flow = SimpleNamespace(_validation_source_by_code={stock.code: False})
    with pytest.raises(ValueError, match="SOR_SOURCE_UNAVAILABLE"):
        flow_module.IndicatorFollowSignalValidationV5Flow._validation_market_data_identity(
            flow, stock,
        )
    snapshot = flow_module.ValidationHistoricalSnapshot(
        stock=stock, timeframe_minutes=5, timeframe_key="M5",
        requested_count=1, request_id="krx-1", rows=[],
        market_data_identity=stock.code, market_source="KRX",
    )
    result = flow_module.ValidationHistoricalResult(ok=True, snapshot=snapshot)
    assert not flow_module.IndicatorFollowSignalValidationV5Flow._historical_result_matches_validation_source(
        flow, result, stock,
    )


@pytest.mark.parametrize(
    ("timeframe", "minutes", "times"),
    [
        ("M1", 1, ["20261019081200", "20261019091300", "20261019161200"]),
        ("M3", 3, ["20261019080000", "20261019090000", "20261019153900"]),
        ("M5", 5, ["20261019080000", "20261019090500", "20261019154000"]),
        ("M10", 10, ["20261019080000", "20261019090000", "20261019154000"]),
        ("M15", 15, ["20261019080000", "20261019091500", "20261019154500"]),
        ("M30", 30, ["20261019080000", "20261019093000", "20261019154000"]),
        ("M60", 60, ["20261019080000", "20261019100000", "20261019154000"]),
        ("M120", 120, ["20261019080000", "20261019100000", "20261019154000"]),
        ("M240", 240, ["20261019080000", "20261019120000", "20261019154000"]),
        ("D1", 1, ["20261015000000", "20261016000000", "20261019000000"]),
        ("W1", 1, ["20260928000000", "20261005000000", "20261019000000"]),
        ("MO1", 1, ["20260801000000", "20260901000000", "20261001000000"]),
        ("Y1", 1, ["20240101000000", "20250101000000", "20260101000000"]),
    ],
)
def test_integrated_display_uses_exact_selected_source_ohlcv_and_server_times(timeframe, minutes, times):
    from tests.test_indicator_follow_signal_validation_v5_integrated_history_scroll import _flow
    from routines.지표추종매매.routine_validation_contract import ValidationRequest, ValidationSettingsSnapshot
    from routines.지표추종매매.routine_validation_session import ValidationSession

    flow = _flow()
    stock = ValidationStockRef("005930", "Samsung")
    settings = ValidationSettingsSnapshot({
        "bar": {"bar_minutes": minutes}, "validation_timeframe": {"key": timeframe}
    })
    session = ValidationSession(ValidationRequest(stock, settings, minutes), operation_active_reader=lambda: False)
    candles = [
        {"time": stamp, "open": 100 + i, "high": 110 + i,
         "low": 90 + i, "close": 103 + i, "volume": 1000 + i}
        for i, stamp in enumerate(times)
    ]
    flow._integrated_display_sidecars[flow._integrated_display_sidecar_key(session)] = {
        "candles": [{**row, "close": -1} for row in candles], "verified_nxt_eligible": True,
    }
    pool = {
        "stock": stock, "candles": candles, "market_source": "INTEGRATED",
        "market_data_identity": "005930_AL", "timeframe_key": timeframe,
        "timeframe_minutes": minutes, "requested_count": 3,
    }
    view = flow._validation_pool_for_session(session, pool, target_count=3)
    assert view["candles"] == candles
    assert view.get("display_candles", view["candles"]) == candles
    assert [row["time"] for row in view["candles"]] == times


def test_selected_integrated_source_may_request_verified_nxt_pre_asynchronously():
    from tests.test_indicator_follow_signal_validation_v5_integrated_history_scroll import _flow, _session

    flow = _flow()
    session = _session()
    flow._validation_source_by_code[session.request.stock.code] = True
    calls = []
    flow._broker = SimpleNamespace(
        request_minute_candles_display_only_integrated=lambda *args, **kwargs: calls.append((args, kwargs))
    )
    with patch.object(flow, "_verified_nxt_display_stock", return_value=True):
        flow._ensure_integrated_display_sidecar(SimpleNamespace(), session, 5900)
    assert len(calls) == 1
    assert calls[0][0] == ("005930", "Samsung")
    assert calls[0][1]["interval"] == 1
    assert calls[0][1]["nxt_available"] is True
    assert callable(calls[0][1]["callback"])


def test_selected_period_request_keeps_period_key_and_al_provenance():
    calls = []

    class Broker:
        def request_period_candles_read_only_integrated(self, code, name, **kwargs):
            calls.append((code, kwargs["timeframe_key"], kwargs["nxt_available"]))
            kwargs["callback"]({
                "ok": True, "type": "period_candles", "code": code,
                "timeframe_key": kwargs["timeframe_key"], "request_id": "period-1",
                "rqname": "period-1", "market_data_identity": "005930_AL",
                "market_source": "INTEGRATED", "rows": [], "rows_count": 0,
                "trcode": "opt10094",
            })
            return {"ok": True, "rqname": "period-1"}

    requester = flow_module._V5SelectedSourceRequester(
        Broker(), ValidationStockRef("005930", "Samsung"), "Y1", True
    )
    results = []
    requester.request_period_candles_read_only(
        "005930", "Samsung", timeframe_key="Y1", count=120, callback=results.append
    )
    assert calls == [("005930", "Y1", True)]
    assert results[0]["ok"] is True


def _period_capture_request(tmp_path, timeframe, period_start, start_hour=20,
                            response_override=None, capture_day=21,
                            callback_epoch=7):
    cache = SimpleNamespace(root=tmp_path)
    capture = lambda *args: flow_module.IndicatorFollowSignalValidationV5Flow._capture_sor_period_prior_close(
        SimpleNamespace(_historical_cache=cache), *args
    )
    clock = iter((
        datetime(2026, 10, capture_day, start_hour, 1, tzinfo=ZoneInfo("Asia/Seoul")),
        datetime(2026, 10, capture_day, start_hour, 2, tzinfo=ZoneInfo("Asia/Seoul")),
    ))
    trcode = {"W1": "opt10082", "MO1": "opt10083", "Y1": "opt10094"}[timeframe]
    response = {
        "ok": True, "type": "period_candles", "code": "005930",
        "request_id": "period-1", "rqname": "period-1",
        "market_data_identity": "005930_AL", "market_source": "INTEGRATED",
        "timeframe_key": timeframe, "trcode": trcode,
        "has_more": False, "rows_count": 2,
        "rows": [
            {"체결시간": "20250101000000", "시가": "1", "고가": "2",
             "저가": "1", "현재가": "2", "거래량": "3"},
            {"체결시간": period_start, "시가": "10", "고가": "12",
             "저가": "9", "현재가": "11", "거래량": "100"},
        ],
    }
    response.update(response_override or {})

    class Broker:
        def __init__(self):
            self.snapshots = iter((7, callback_epoch))

        def broker_session_snapshot(self):
            return SimpleNamespace(connected=True,
                                   connection_epoch=next(self.snapshots),
                                   login_session_id="login-7")

        def request_period_candles_read_only_integrated(self, code, name, **kwargs):
            kwargs["callback"](response)
            return {"ok": True, "rqname": "period-1"}

    requester = flow_module._V5SelectedSourceRequester(
        Broker(), ValidationStockRef("005930", "Samsung"), timeframe, True,
        on_verified_period_response=capture, now_factory=lambda: next(clock),
    )
    results = []
    requester.request_period_candles_read_only(
        "005930", "Samsung", timeframe_key=timeframe, count=5000,
        callback=results.append,
    )
    return results, tmp_path / f"005930_AL_{timeframe}_prior_close.json"


@pytest.mark.parametrize("timeframe,period_start", [
    ("W1", "20261019000000"), ("MO1", "20261001000000"),
    ("Y1", "20260101000000"),
])
def test_period_prior_close_capture_requires_matched_after_session_broker_response(
    tmp_path, timeframe, period_start,
):
    results, path = _period_capture_request(tmp_path, timeframe, period_start)
    assert results[-1]["ok"] is True
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["period_start"] == period_start
    assert record["capture_date"] == "2026-10-21"
    assert record["request_started_at"] == "2026-10-21T20:01:00+09:00"
    assert record["broker_callback_received_at"] == "2026-10-21T20:02:00+09:00"
    assert record["market_data_identity"] == "005930_AL"
    assert record["timeframe_key"] == timeframe
    assert record["cumulative_ohlcv"] == {
        "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0,
        "volume": 100.0,
    }
    assert record["provenance"] == "SESSION_BOUND_BROKER_AL_POST_CLOSE_OBSERVATION"
    assert record["broker_session"] == {
        "request_connected": True, "callback_connected": True,
        "request_connection_epoch": 7, "request_login_session_id": "login-7",
        "callback_connection_epoch": 7, "callback_login_session_id": "login-7",
    }


@pytest.mark.parametrize("start_hour,override", [
    (19, {}), (20, {"request_id": "wrong"}),
    (20, {"market_data_identity": "005930"}),
    (20, {"timeframe_key": "MO1"}),
    (20, {"has_more": True}),
    (20, {"rows_count": 3}),
])
def test_period_prior_close_capture_skips_unproved_cutoff_or_source(
    tmp_path, start_hour, override,
):
    _results, path = _period_capture_request(
        tmp_path, "W1", "20261019000000", start_hour, override,
    )
    assert not path.exists()


@pytest.mark.parametrize("timeframe,period_start", [
    ("W1", "20261012000000"), ("MO1", "20260901000000"),
    ("Y1", "20250101000000"),
])
def test_period_prior_close_capture_rejects_stale_period_identity(
    tmp_path, timeframe, period_start,
):
    _results, path = _period_capture_request(tmp_path, timeframe, period_start)
    assert not path.exists()


@pytest.mark.parametrize("timeframe", ["W1", "MO1", "Y1"])
def test_captured_period_remains_fail_closed_for_next_day_or_period(tmp_path, timeframe):
    period_start = {"W1": "20261019000000", "MO1": "20261001000000",
                    "Y1": "20260101000000"}[timeframe]
    _results, path = _period_capture_request(tmp_path, timeframe, period_start)
    assert path.exists()
    reader = SimpleNamespace(_historical_cache=SimpleNamespace(root=tmp_path))
    next_period = {"W1": "20261026000000", "MO1": "20261101000000",
                   "Y1": "20270101000000"}[timeframe]
    for direct_period in (period_start, next_period):
        with pytest.raises(ValueError, match="PERIOD_CUMULATIVE_SOURCE_UNAVAILABLE"):
            flow_module.IndicatorFollowSignalValidationV5Flow._sor_period_prior_close(
                reader, "005930", timeframe, direct_period,
                datetime(2026, 10, 25, tzinfo=ZoneInfo("Asia/Seoul")),
            )


@pytest.mark.parametrize("timeframe,period_start", [
    ("W1", "20261019000000"), ("MO1", "20261001000000"),
    ("Y1", "20260101000000"),
])
def test_period_prior_close_joins_today_m1_without_changing_direct_history(
    tmp_path, timeframe, period_start,
):
    _results, path = _period_capture_request(tmp_path, timeframe, period_start)
    assert path.exists()
    flow, original_session, pool, _market, _ = _registered_m1_calculation_case()
    flow._historical_cache = SimpleNamespace(root=tmp_path)
    as_of = datetime(2026, 10, 22, 8, 3, 30, tzinfo=ZoneInfo("Asia/Seoul"))
    session = SimpleNamespace(request=SimpleNamespace(
        stock=original_session.request.stock, timeframe_key=timeframe,
        timeframe_minutes=1,
    ))
    prior = {"time": "20250101000000", "open": 2, "high": 3,
             "low": 1, "close": 2, "volume": 5}
    current = {"time": period_start, "open": 999, "high": 999,
               "low": 999, "close": 999, "volume": 999}
    pool.update(candles=[prior, current], timeframe_key=timeframe)
    rows = [
        {"bar_time": f"2026-10-22T08:0{i}:00+09:00", "open": 11 + i,
         "high": 12 + i, "low": 10 + i, "close": 11 + i,
         "volume": i + 1, "is_complete": i != 3}
        for i in range(4)
    ]
    projected = {
        "available": True, "projection": "FORMING_BASE_BAR",
        "complete": False, "forming_included": True,
        "completeness": {"forming_included": True},
        "forming_snapshot_source": "BROKER_AL_TR_FORMING_M1",
        "forming_snapshot_received_at": as_of.isoformat(),
        "calculation_only": True, "calculation_source": "BROKER_AL_CONFIRMED_M1",
        "source_proof": "BROKER_ROWS_NOT_INDEPENDENT_MERGE_ATTESTATION",
        "market_source": "INTEGRATED", "market_data_identity": "005930_AL",
        "timeframe_minutes": 1,
        "freshness": {"as_of": as_of.isoformat(timespec="seconds")},
        "candles": rows, "source_identity": "confirmed-m1",
    }
    frozen = (flow._pool_computation_source_identity(pool), as_of, projected)
    view = flow._sor_m1_calculation_view(session, pool, pool, frozen)
    assert view["candles"] == [prior, {
        "time": period_start, "open": 10.0, "high": 15,
        "low": 9.0, "close": 14, "volume": 110.0,
    }]
    assert view["display_candles"] == [prior, current]
    assert pool["candles"] == [prior, current]
    assert view["snapshot"].to_rows() == flow._historical_rows_from_candles(view["candles"])
    live = flow._sor_m1_calculation_view(session, pool, pool, frozen, live_calendar=True)
    assert live["display_candles"] == live["candles"]
    assert live["candles"][-1]["volume"] == 110.0
    assert pool["candles"] == [prior, current]
    flow._historical_cache = SimpleNamespace(root=tmp_path / "missing")
    with pytest.raises(ValueError, match="PERIOD_CUMULATIVE_SOURCE_UNAVAILABLE"):
        flow._sor_m1_calculation_view(session, pool, pool, frozen, live_calendar=True)
    flow._historical_cache = SimpleNamespace(root=tmp_path)
    assert flow._sor_m1_calculation_view(
        session, pool, dict(pool, candles=[prior], display_candles=[prior]), frozen,
    )["candles"] == [prior]
    projected["candles"] = rows[1:]
    with pytest.raises(ValueError, match="M1_COVERAGE_INCOMPLETE"):
        flow._sor_m1_calculation_view(session, pool, pool, frozen)

    first_day = {"W1": "2026-10-26", "MO1": "2026-11-02",
                 "Y1": "2027-01-04"}[timeframe]
    new_as_of = datetime.strptime(first_day, "%Y-%m-%d").replace(
        hour=8, minute=3, second=30, tzinfo=ZoneInfo("Asia/Seoul"),
    )
    current["time"] = new_as_of.strftime("%Y%m%d000000")
    projected["candles"] = [
        dict(row, bar_time=f"{first_day}T08:0{i}:00+09:00")
        for i, row in enumerate(rows)
    ]
    projected["forming_snapshot_received_at"] = new_as_of.isoformat()
    projected["freshness"] = {"as_of": new_as_of.isoformat(timespec="seconds")}
    new_frozen = (flow._pool_computation_source_identity(pool), new_as_of, projected)
    new_view = flow._sor_m1_calculation_view(session, pool, pool, new_frozen)
    assert new_view["candles"] == [prior, {
        "time": current["time"], "open": 11, "high": 15,
        "low": 10, "close": 14, "volume": 10,
    }]


@pytest.mark.parametrize("timeframe,historic,current,future", [
    ("W1", "20261012000000", "20261019000000", "20261026000000"),
    ("MO1", "20260901000000", "20261001000000", "20261101000000"),
    ("Y1", "20250102000000", "20260102000000", "20270102000000"),
])
def test_entire_historical_period_pool_keeps_direct_sor_view(
    timeframe, historic, current, future,
):
    cls = flow_module.IndicatorFollowSignalValidationV5Flow
    flow = SimpleNamespace(_pool_computation_source_identity=cls._pool_computation_source_identity)
    session = SimpleNamespace(request=SimpleNamespace(
        timeframe_key=timeframe, stock=SimpleNamespace(code="005930"),
    ))
    pool = {"candles": [{"time": historic, "close": 1}], "timeframe_key": timeframe}
    as_of = datetime(2026, 10, 22, tzinfo=ZoneInfo("Asia/Seoul"))
    projected = {"available": False, "availability_state": "FRESH_M1_REQUIRED"}
    frozen = (flow._pool_computation_source_identity(pool), as_of, projected)

    assert cls._sor_m1_calculation_view(flow, session, pool, pool, frozen) is pool
    with pytest.raises(ValueError, match="DISPLAYED_CALCULATION_CHANGED"):
        cls._sor_m1_calculation_view(
            flow, session, pool, pool, frozen, [dict(pool["candles"][0], close=2)],
        )
    with pytest.raises(ValueError, match="DISPLAYED_CALCULATION_CHANGED"):
        cls._sor_m1_calculation_view(
            flow, session, pool,
            dict(pool, display_candles=[dict(pool["candles"][0], close=2)]), frozen,
        )
    pool["candles"][0]["close"] = 2
    with pytest.raises(ValueError, match="SOURCE_CHANGED_AFTER_SNAPSHOT"):
        cls._sor_m1_calculation_view(flow, session, pool, pool, frozen)

    for stamp, error in ((future, "PERIOD_IDENTITY_INVALID"),
                         (current, "FRESH_M1_REQUIRED")):
        pool["candles"][0]["time"] = stamp
        frozen = (flow._pool_computation_source_identity(pool), as_of, projected)
        with pytest.raises(ValueError, match=error):
            cls._sor_m1_calculation_view(flow, session, pool, pool, frozen)


def test_period_prior_close_rejects_changed_epoch_and_accepts_friday_monday(tmp_path):
    period_start = "20261001000000"
    _results, path = _period_capture_request(
        tmp_path, "MO1", period_start, capture_day=16,
    )
    reader = SimpleNamespace(_historical_cache=SimpleNamespace(root=tmp_path))
    read = flow_module.IndicatorFollowSignalValidationV5Flow._sor_period_prior_close
    monday = datetime(2026, 10, 19, 8, 3, tzinfo=ZoneInfo("Asia/Seoul"))
    assert read(reader, "005930", "MO1", period_start, monday)["volume"] == 100
    with pytest.raises(ValueError, match="PERIOD_CUMULATIVE_SOURCE_UNAVAILABLE"):
        read(reader, "005930", "MO1", "20261102000000", monday)
    with pytest.raises(ValueError, match="PERIOD_CUMULATIVE_SOURCE_UNAVAILABLE"):
        read(reader, "005930", "MO1", period_start, monday - timedelta(days=2))
    with pytest.raises(ValueError, match="PERIOD_CUMULATIVE_SOURCE_UNAVAILABLE"):
        read(reader, "005930", "MO1", period_start, monday + timedelta(days=1))
    record = json.loads(path.read_text(encoding="utf-8"))
    record["broker_session"]["callback_connection_epoch"] = 8
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="PERIOD_CUMULATIVE_SOURCE_UNAVAILABLE"):
        read(reader, "005930", "MO1", period_start, monday)
    record["broker_session"]["callback_connection_epoch"] = 7
    record["request_started_at"] = "2026-10-16T19:59:00+09:00"
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="PERIOD_CUMULATIVE_SOURCE_UNAVAILABLE"):
        read(reader, "005930", "MO1", period_start, monday)


def test_period_capture_with_changed_broker_epoch_remains_untrusted(tmp_path):
    _results, path = _period_capture_request(
        tmp_path, "W1", "20261019000000", callback_epoch=8,
    )
    record = json.loads(path.read_text(encoding="utf-8"))
    assert "broker_session" not in record
    reader = SimpleNamespace(_historical_cache=SimpleNamespace(root=tmp_path))
    with pytest.raises(ValueError, match="PERIOD_CUMULATIVE_SOURCE_UNAVAILABLE"):
        flow_module.IndicatorFollowSignalValidationV5Flow._sor_period_prior_close(
            reader, "005930", "W1", "20261019000000",
            datetime(2026, 10, 22, tzinfo=ZoneInfo("Asia/Seoul")),
        )


def test_unknown_eligibility_invalidates_inflight_v5_work():
    from indicator_follow_signal_validation_projection import IndicatorFollowSignalValidationRunRequest
    from routines.지표추종매매.routine_validation_contract import ValidationSettingsSnapshot
    from tests.test_indicator_follow_signal_validation_v5_integrated_history_scroll import _flow
    from tests.test_indicator_follow_signal_validation_persistent_history_flow import _Window

    flow = _flow()
    stock = ValidationStockRef("005930", "Samsung")
    window = _Window(stock)
    flow._open_windows[id(window)] = window
    flow._request_generation[id(window)] = 7
    flow._validation_source_by_code[stock.code] = True
    request = IndicatorFollowSignalValidationRunRequest(
        ValidationSettingsSnapshot({"bar": {"bar_minutes": 5}}), 10
    )
    snapshot = SimpleNamespace(state=flow_module.STOCK_LIBRARY_READY, records=[])
    with (
        patch.object(flow_module, "load_stock_library_snapshot", return_value=snapshot),
        patch.object(flow, "_invalidate_window_requests") as invalidate,
        patch.object(flow, "_fail_window") as fail,
    ):
        flow._run_validation(window, request)
    invalidate.assert_called_once_with(id(window))
    fail.assert_called_once_with(window, "NXT_ELIGIBILITY_NOT_VERIFIED")
    assert stock.code not in flow._validation_source_by_code


@pytest.mark.parametrize("integrated", [True, False])
def test_v5_initial_run_requests_selected_source_once_with_full_history_envelope(integrated):
    from indicator_follow_signal_validation_projection import IndicatorFollowSignalValidationRunRequest
    from routines.지표추종매매.routine_validation_contract import ValidationSettingsSnapshot
    from tests.test_indicator_follow_signal_validation_v5_integrated_history_scroll import _flow
    from tests.test_indicator_follow_signal_validation_persistent_history_flow import _Window

    flow = _flow()
    stock = ValidationStockRef("005930", "Samsung")
    window = _Window(stock)
    flow._open_windows[id(window)] = window
    flow._request_generation[id(window)] = 0
    request = IndicatorFollowSignalValidationRunRequest(
        ValidationSettingsSnapshot({"bar": {"bar_minutes": 5}}), 10
    )
    calls = []
    rows = flow._historical_rows_from_candles([{
        "time": "20261016090500", "open": 100, "high": 105,
        "low": 99, "close": 103, "volume": 1000,
    }])

    class Broker:
        def request_minute_candles_display_only_integrated(self, code, name, **kwargs):
            calls.append(("AL", kwargs["interval"], kwargs["count"], kwargs["nxt_available"]))
            kwargs["callback"]({
                "ok": True, "type": "minute_candles", "code": code,
                "interval": kwargs["interval"], "request_id": "al-req",
                "rqname": "al-req", "market_data_identity": "005930_AL",
                "market_source": "INTEGRATED", "rows": rows, "rows_count": len(rows),
                "trcode": "opt10080",
            })
            return {"ok": True, "rqname": "al-req"}

        def request_minute_candles_read_only(self, code, name, **kwargs):
            calls.append(("KRX", kwargs["interval"], kwargs["count"], None))
            kwargs["callback"]({
                "ok": True, "type": "minute_candles", "code": code,
                "interval": kwargs["interval"], "request_id": "krx-req",
                "rqname": "krx-req", "market_data_identity": "005930",
                "market_source": "KRX", "rows": rows, "rows_count": len(rows),
                "trcode": "opt10080",
            })
            return {"ok": True, "rqname": "krx-req"}

    flow._broker = Broker()
    snapshot = SimpleNamespace(
        state=flow_module.STOCK_LIBRARY_READY,
        records=[{"code": "005930", "nxt_available": integrated}],
    )
    with (
        patch.object(flow_module, "load_stock_library_snapshot", return_value=snapshot),
        patch.object(flow, "_handle_historical_result") as handle,
        patch.object(flow, "_fail_window") as fail,
    ):
        flow._run_validation(window, request)
    if not integrated:
        fail.assert_called_once_with(window, "SOR_SOURCE_UNAVAILABLE")
        assert calls == []
        handle.assert_not_called()
        return
    fail.assert_not_called()
    assert len(calls) == 1 and calls[0][0] == ("AL" if integrated else "KRX")
    assert calls[0][1] == 5 and calls[0][2] == 5900
    assert calls[0][3] is (True if integrated else None)
    assert handle.call_count == 1
    assert handle.call_args.args[2].snapshot.market_data_identity == (
        "005930_AL" if integrated else "005930"
    )


def test_v5_rejects_legacy_krx_calculation_setting_before_broker_request():
    from indicator_follow_signal_validation_projection import IndicatorFollowSignalValidationRunRequest
    from tests.test_indicator_follow_signal_validation_v5_integrated_history_scroll import _flow
    from tests.test_indicator_follow_signal_validation_persistent_history_flow import _Window

    flow = _flow()
    stock = ValidationStockRef("005930", "Samsung")
    window = _Window(stock)
    calls = []
    flow._open_windows[id(window)] = window
    flow._request_generation[id(window)] = 0
    flow._broker = SimpleNamespace(
        request_minute_candles_read_only=lambda *args, **kwargs: calls.append("KRX"),
        request_minute_candles_display_only_integrated=lambda *args, **kwargs: calls.append("AL"),
    )
    snapshot = SimpleNamespace(
        state=flow_module.STOCK_LIBRARY_READY,
        records=[{"code": stock.code, "nxt_available": True}],
    )
    with (
        patch.object(flow_module, "load_stock_library_snapshot", return_value=snapshot),
        patch.object(flow_module, "read_json_dict", return_value={"calculation_market_source": "KRX"}),
        patch.object(flow, "_fail_window") as fail,
    ):
        flow._run_validation(window, IndicatorFollowSignalValidationRunRequest(
            flow_module.ValidationSettingsSnapshot({"bar": {"bar_minutes": 5}}), 10,
        ))
    fail.assert_called_once_with(window, "CALCULATION_MARKET_SOURCE_INVALID")
    assert calls == []


@pytest.mark.parametrize(
    ("integrated", "timeframe", "retain", "complete_metadata"),
    [
        (True, 1, True, True),
        (True, 1, True, False),
        (True, 1, False, True),
        (False, 1, True, True),
        (True, 5, True, True),
    ],
)
def test_v5_collection_retains_only_opted_in_broker_al_m1_envelopes(
    tmp_path, integrated, timeframe, retain, complete_metadata,
):
    from indicator_follow_signal_validation_projection import IndicatorFollowSignalValidationRunRequest
    from routines.지표추종매매.routine_validation_contract import ValidationSettingsSnapshot
    from tests.test_indicator_follow_signal_validation_persistent_history_flow import (
        _Host, _Window, _legacy_session_unavailable,
    )

    stock = ValidationStockRef("005930", "Samsung")
    request_id = "opt10080_005930_090231000000"
    source = "005930_AL" if integrated else stock.code
    market_source = "INTEGRATED" if integrated else "KRX"
    started = {
        "ok": True, "rqname": request_id, "code": stock.code,
        "market_data_identity": source, "market_source": market_source,
        "display_only": integrated, "connection_epoch": 7,
        "login_session_id": "login-1",
        "request_as_of": "2026-10-20T09:02:30+09:00",
        "started_at": "2026-10-20T09:02:30+09:00",
    }
    receipt = {
        "ok": True, "type": "minute_candles", "code": stock.code,
        "interval": timeframe, "request_id": request_id, "rqname": request_id,
        "trcode": "opt10080", "market_data_identity": source,
        "market_source": market_source, "display_only": integrated,
        "connection_epoch": 7, "login_session_id": "login-1",
        "received_at": "2026-10-20T09:02:40+09:00", "has_more": False,
        "rows": [{"체결시간": "20261020090200", "현재가": "100"}],
        "rows_count": 1,
    }
    if not complete_metadata:
        receipt.pop("received_at")
    calls = []

    class Broker:
        def request_minute_candles_display_only_integrated(self, code, name, **kwargs):
            calls.append(("AL", kwargs["interval"]))
            kwargs["callback"](receipt)
            return started

        def request_minute_candles_read_only(self, code, name, **kwargs):
            calls.append(("KRX", kwargs["interval"]))
            kwargs["callback"](receipt)
            return started

    flow = flow_module.IndicatorFollowSignalValidationV5Flow(
        Broker(), host=_Host(),
        historical_cache=IndicatorFollowSignalValidationHistoricalCache(tmp_path),
        production_session_contract_reader=_legacy_session_unavailable,
        retain_broker_al_m1_evidence=retain,
    )
    window = _Window(stock)
    flow._open_windows[id(window)] = window
    flow._request_generation[id(window)] = 0
    request = IndicatorFollowSignalValidationRunRequest(
        ValidationSettingsSnapshot({"bar": {"bar_minutes": timeframe}}),
        10, force_historical_refresh=True,
    )
    eligibility = SimpleNamespace(
        state=flow_module.STOCK_LIBRARY_READY,
        records=[{"code": stock.code, "nxt_available": integrated}],
    )
    with (
        patch.object(flow_module, "load_stock_library_snapshot", return_value=eligibility),
        patch.object(flow, "_handle_historical_result") as handle,
        patch.object(flow, "_fail_window") as fail,
    ):
        flow._run_validation(window, request)
    if not integrated:
        fail.assert_called_once_with(window, "SOR_SOURCE_UNAVAILABLE")
        assert calls == []
        handle.assert_not_called()
        assert flow.broker_al_m1_request_receipts(window) == ()
        return
    fail.assert_not_called()
    assert calls == [("AL" if integrated else "KRX", timeframe)]
    assert handle.call_count == 1
    retained = flow.broker_al_m1_request_receipts(window)
    if (integrated, timeframe, retain, complete_metadata) == (True, 1, True, True):
        assert retained == ((started, receipt),)
        retained[0][1]["rows"][0]["현재가"] = "changed"
        assert flow.broker_al_m1_request_receipts(window)[0][1]["rows"][0]["현재가"] == "100"
    else:
        assert retained == ()
    assert handle.call_args.args[2].broker_al_request_receipt == (
        (started, receipt) if retained else None
    )


@pytest.mark.parametrize("invalid_mode", ["wrong_source", "exception", "failed_response"])
def test_invalid_al_refresh_fails_without_cached_or_krx_install(tmp_path, invalid_mode):
    from candle_timeframe_aggregation import SEOUL_TIMEZONE
    from indicator_follow_signal_validation_projection import IndicatorFollowSignalValidationRunRequest
    from indicator_follow_validation_history_contract import required_validation_history_context_bars
    from routines.지표추종매매.routine_validation_contract import ValidationSettingsSnapshot
    from tests.test_indicator_follow_signal_validation_persistent_history_flow import _Host, _Window, _legacy_session_unavailable
    from tests.test_indicator_follow_signal_validation_v5_refresh_probe_timing import _rows, _result

    stock = ValidationStockRef("005930", "Samsung")
    settings = ValidationSettingsSnapshot({"bar": {"bar_minutes": 5}})
    requested = 120 + required_validation_history_context_bars(settings.to_dict())
    calls = []

    class Provider:
        def __init__(self, session, requester):
            pass

        def request_latest(self, count, callback):
            calls.append(count)
            if invalid_mode == "exception":
                raise RuntimeError("broker failed")
            if invalid_mode == "failed_response":
                callback(flow_module.ValidationHistoricalResult(
                    False, reason="HISTORICAL_REQUEST_FAILED", error="broker failed",
                ))
                return
            callback(_result(stock, _rows(1), source="KRX", identity=stock.code))

    flow = flow_module.IndicatorFollowSignalValidationV5Flow(
        object(), host=_Host(), historical_count=120,
        historical_provider_factory=Provider,
        historical_cache=IndicatorFollowSignalValidationHistoricalCache(tmp_path),
        production_session_contract_reader=_legacy_session_unavailable,
    )
    flow._now_factory = lambda: datetime.now(SEOUL_TIMEZONE)
    flow._validation_source_by_code[stock.code] = True
    assert flow._persist_history(
        stock, 5, "M5", requested, _rows(requested),
        market_data_identity="005930_AL", market_source="INTEGRATED",
    )
    window = _Window(stock)
    flow._open_windows[id(window)] = window
    flow._request_generation[id(window)] = 0
    snapshot = SimpleNamespace(
        state=flow_module.STOCK_LIBRARY_READY,
        records=[{"code": stock.code, "nxt_available": True}],
    )
    with (
        patch.object(flow_module, "load_stock_library_snapshot", return_value=snapshot),
        patch.object(flow, "_install_acquired_pool") as install,
        patch.object(flow, "_fail_window") as fail,
    ):
        flow._run_validation(window, IndicatorFollowSignalValidationRunRequest(
            settings, 10, force_historical_refresh=True,
        ))
    assert calls == [requested]
    install.assert_not_called()
    fail.assert_called_once_with(
        window,
        "HISTORICAL_REQUEST_ERROR: broker failed"
        if invalid_mode == "exception" else
        "HISTORICAL: HISTORICAL_REQUEST_FAILED (broker failed)"
        if invalid_mode == "failed_response" else
        "HISTORICAL: MARKET_SOURCE_IDENTITY_INVALID",
    )
def test_mock_sor_observation_registers_al_m1_without_losing_standby(monkeypatch):
    from gui_market_data_host import MarketDataHost

    market = SimpleNamespace(
        _candle_observation_stock_codes=(),
        _candle_observation_required_by_stock={},
        _candle_observation_timeframe_requirements_by_stock={},
        _candle_standby_stock_codes=(),
        _sor_m1_required_by_stock={"000660": 500},
        _sor_standby_m1_required_by_stock={"000660": 500},
        _sor_observation_m1_required_by_stock={},
        _production_monitoring_stock_codes=(),
        _execution_shadow_stock_codes=(),
        kiwoom_api=SimpleNamespace(sync_realtime_shadow_targets=lambda _targets: {"ok": True}),
    )
    market.sync_monitoring_targets = lambda _targets: {"shadow_sync": {"ok": True}}
    market.refresh_operation_candles = lambda _minute: None
    monkeypatch.setattr("gui_market_data_host.QTimer.singleShot", lambda *_args: None)
    requirement = {"stock_code": "005930", "rules": {"bar": {"bar_minutes": 5}},
                   "projection_request": {"projection": "FORMING_BASE_BAR", "warmup_bars": 5},
                   "calculation_market_source": "SOR"}

    MarketDataHost.sync_candle_observation_targets(market, [requirement])
    assert "005930" in market._sor_m1_required_by_stock
    assert market._sor_m1_required_by_stock["000660"] == 500
    assert MarketDataHost.direct_timeframe_requirements(market, "005930")[1] > 0

    MarketDataHost.sync_candle_standby_requirements(market, [{
        **requirement, "stock_code": "000660",
    }])
    assert "005930" in market._sor_m1_required_by_stock
    assert market._sor_m1_required_by_stock["000660"] == 6

    MarketDataHost.sync_candle_observation_targets(market, [])
    assert market._sor_m1_required_by_stock == market._sor_standby_m1_required_by_stock
