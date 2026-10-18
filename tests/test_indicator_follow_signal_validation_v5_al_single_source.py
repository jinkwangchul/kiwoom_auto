"""V5 selected-source contracts without a live broker."""

from datetime import datetime
import json
from zoneinfo import ZoneInfo
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import gui_indicator_follow_signal_validation_v5_flow as flow_module
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

    requester = flow_module._V5SelectedSourceRequester(
        Broker(), ValidationStockRef("005930", "Samsung"), "M5", integrated
    )
    results = []
    requester.request_minute_candles_read_only(
        "005930", "Samsung", interval=5, count=5900, callback=results.append
    )
    assert calls == [("AL" if integrated else "KRX", "005930", 5, True if integrated else None)]
    assert results[0]["ok"] is True


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
    for eligibility, identity in ((True, "005930_AL"), (False, "005930")):
        flow = SimpleNamespace(_validation_source_by_code={"005930": eligibility})
        assert flow_type._validation_market_data_identity(flow, stock) == identity
    with pytest.raises(ValueError, match="NXT_ELIGIBILITY_NOT_VERIFIED"):
        flow_type._validation_market_data_identity(
            SimpleNamespace(_validation_source_by_code={"005930": None}), stock
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


def test_selected_integrated_source_does_not_start_legacy_second_request():
    from tests.test_indicator_follow_signal_validation_v5_integrated_history_scroll import _flow, _session

    flow = _flow()
    session = _session()
    flow._validation_source_by_code[session.request.stock.code] = True
    calls = []
    flow._broker = SimpleNamespace(
        request_minute_candles_display_only_integrated=lambda *args, **kwargs: calls.append((args, kwargs))
    )
    flow._ensure_integrated_display_sidecar(SimpleNamespace(), session, 5900)
    assert calls == []


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
    fail.assert_not_called()
    assert len(calls) == 1 and calls[0][0] == ("AL" if integrated else "KRX")
    assert calls[0][1] == 5 and calls[0][2] == 5900
    assert calls[0][3] is (True if integrated else None)
    assert handle.call_count == 1
    assert handle.call_args.args[2].snapshot.market_data_identity == (
        "005930_AL" if integrated else "005930"
    )


@pytest.mark.parametrize("invalid_mode", ["wrong_source", "exception"])
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
        flow._run_validation(window, IndicatorFollowSignalValidationRunRequest(settings, 10))
    assert calls == [300]
    install.assert_not_called()
    fail.assert_called_once_with(
        window,
        "HISTORICAL_REQUEST_ERROR: broker failed"
        if invalid_mode == "exception" else "HISTORICAL: MARKET_SOURCE_IDENTITY_INVALID",
    )
