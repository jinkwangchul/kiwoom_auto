"""Real MarketDataHost entrypoint: broker _AL M1 -> calculation-only source.

No broker I/O, new TR dispatch, GUI startup or live order.
"""
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from candle_timeframe_aggregation import SEOUL_TIMEZONE
from gui_market_data_host import MarketDataHost
from tests.test_display_only_integrated_history import (
    DisplayOnlyMarketDataHostTest, _opt10080_row, _calculation_minute,
)


NOW = datetime(2026, 10, 20, 9, 5, tzinfo=SEOUL_TIMEZONE)


def _installed_host():
    host = DisplayOnlyMarketDataHostTest()._host()
    rows = [_opt10080_row(f"20261020090{i}00", 100 + i) for i in range(5)]
    with patch(
        "gui_market_data_host.load_stock_library_snapshot",
        return_value=DisplayOnlyMarketDataHostTest._snapshot(True),
    ), patch.object(
        MarketDataHost, "direct_timeframe_requirements", return_value={1: 5},
    ):
        installed = MarketDataHost.install_display_timeframe_candles(
            host, "005930", 1, rows, as_of=NOW,
            market_data_identity="005930_AL", market_source="INTEGRATED",
            display_only=True, connection_epoch=1, login_session_id="SESSION-1",
        )
    assert installed["ok"] is True
    assert installed["stored_count"] == 5
    return host


def _project(host, minutes, *, projection="COMPLETED_TIMEFRAME",
             count=None, scope="SOR_CALCULATION_ONLY", as_of=NOW):
    request = {"projection": projection}
    if count is not None:
        request["warmup_bars"] = count
    with patch(
        "gui_market_data_host.load_stock_library_snapshot",
        return_value=DisplayOnlyMarketDataHostTest._snapshot(True),
    ):
        return MarketDataHost.project_routine_candles(
            host, stock_code="005930",
            rules={"bar": {"bar_minutes": minutes}},
            projection_request=request, as_of=as_of,
            consumer_scope=scope,
        )


def _install_transition_rows(host, stamps, **evidence):
    receipt = {
        "market_data_identity": "005930_AL", "market_source": "INTEGRATED",
        "display_only": True, "connection_epoch": 1,
        "login_session_id": "SESSION-1",
    }
    receipt.update(evidence)
    with patch(
        "gui_market_data_host.load_stock_library_snapshot",
        return_value=DisplayOnlyMarketDataHostTest._snapshot(True),
    ), patch.object(
        MarketDataHost, "direct_timeframe_requirements", return_value={1: 5},
    ):
        return MarketDataHost.install_display_timeframe_candles(
            host, "005930", 1,
            [_opt10080_row(stamp, 100 + index) for index, stamp in enumerate(stamps)],
            as_of=datetime(2026, 10, 20, 15, 44, tzinfo=SEOUL_TIMEZONE),
            **receipt,
        )


def test_sparse_broker_al_transition_rows_are_preserved_and_projected():
    host = DisplayOnlyMarketDataHostTest()._host()
    stamps = ("20261020151900", "20261020153000", "20261020153500",
              "20261020154000", "20261020154100")
    installed = _install_transition_rows(host, stamps)
    assert installed["ok"] is True, installed
    stored = host._display_timeframe_candles[("005930", 1)]
    assert [str(row["bar_time"])[11:16] for row in stored] == [
        "15:19", "15:30", "15:35", "15:40", "15:41",
    ]
    assert [row["session"] for row in stored] == [
        "KRX", "AL_TRANSITION", "AL_TRANSITION", "NXT_AFTER", "NXT_AFTER",
    ]
    assert [(row["open"], row["high"], row["low"], row["close"], row["volume"])
            for row in stored] == [
        (100 + i, 102 + i, 98 + i, 101 + i, 10) for i in range(5)
    ]
    projected = _project(
        host, 1, count=4,
        as_of=datetime(2026, 10, 20, 15, 42, tzinfo=SEOUL_TIMEZONE),
    )
    assert projected["available"] is True, (
        projected.get("availability_state"), projected.get("reason"),
        projected.get("available_minute_candles"),
        projected.get("available_timeframe_bars"),
    )
    assert [str(row["bar_time"])[11:16] for row in projected["candles"]] == [
        "15:19", "15:30", "15:35", "15:40", "15:41",
    ]
    assert [(row["open"], row["high"], row["low"], row["close"], row["volume"])
            for row in projected["candles"]] == [
        (row["open"], row["high"], row["low"], row["close"], row["volume"])
        for row in stored
    ]
    assert projected["market_data_identity"] == "005930_AL"
    assert projected["calculation_only"] is True


def test_missing_nxt_after_minute_still_blocks_projection():
    host = DisplayOnlyMarketDataHostTest()._host()
    assert _install_transition_rows(host, (
        "20261020151900", "20261020153000", "20261020153500",
        "20261020154000", "20261020154100", "20261020154300",
    ))["ok"] is True
    result = _project(
        host, 1, as_of=datetime(2026, 10, 20, 15, 44, tzinfo=SEOUL_TIMEZONE),
    )
    assert result["available"] is False
    assert result["availability_state"] == "SOR_M1_INTERNAL_COVERAGE_GAP"


@pytest.mark.parametrize("evidence", (
    {"market_data_identity": "005930"},
    {"connection_epoch": 2},
    {"login_session_id": "STALE-SESSION"},
))
def test_transition_rows_reject_unverified_or_stale_receipt(evidence):
    host = DisplayOnlyMarketDataHostTest()._host()
    result = _install_transition_rows(host, ("20261020153000",), **evidence)
    assert result == {"ok": False, "reason_code": "DISPLAY_TIMEFRAME_EVIDENCE_INVALID"}
    assert host._display_timeframe_candles == {}


def test_transition_does_not_start_recurring_forming_refresh():
    host = DisplayOnlyMarketDataHostTest()._host()
    host._sor_m1_required_by_stock = {"005930": 1}
    host._display_timeframe_candles[("005930", 1)] = tuple(
        {"bar_time": "2026-10-20T15:29:00+09:00"} for _ in range(700)
    )
    host._display_timeframe_session_identity[("005930", 1)] = (1, "SESSION-1")
    with patch(
        "gui_market_data_host.load_stock_library_snapshot",
        return_value=DisplayOnlyMarketDataHostTest._snapshot(True),
    ), patch.object(
        MarketDataHost, "direct_timeframe_requirements", return_value={1: 700},
    ):
        assert MarketDataHost.display_timeframe_refresh_specs(
            host, "005930", "2026-10-20 15:35",
        ) == ()


def test_existing_al_m1_display_rows_feed_m1_calculation():
    host = _installed_host()
    original = deepcopy(host._display_timeframe_candles)
    result = _project(host, 1, count=4)
    assert result["available"] is True, (result.get("availability_state"), result.get("reason"), result.get("available_minute_candles"), result.get("available_timeframe_bars"))
    assert result["market_source"] == "INTEGRATED"
    assert result["market_data_identity"] == "005930_AL"
    assert result["calculation_source"] == "BROKER_AL_CONFIRMED_M1"
    assert result["source_proof"] == "BROKER_ROWS_NOT_INDEPENDENT_MERGE_ATTESTATION"
    assert result["calculation_only"] is True
    assert result["execution_enabled"] is False
    assert result["send_order_connected"] is False
    assert len(result["candles"]) == 5
    assert result["candles"][0]["open"] == 100
    assert result["candles"][-1]["close"] == 105
    assert host._display_timeframe_candles == original


def test_sor_declared_m1_history_target_gates_availability():
    host = _installed_host()
    original = deepcopy(host._display_timeframe_candles)
    host.kiwoom_api.request_minute_candles_display_only_integrated = (
        lambda *_args, **_kwargs: pytest.fail("Unexpected broker TR")
    )
    request = {"projection": "COMPLETED_TIMEFRAME", "warmup_bars": 1,
               "history_target_bars": 6}

    def project():
        return MarketDataHost.project_broker_sor_m1_calculation(
            host, stock_code="005930", rules={"bar": {"bar_minutes": 1}},
            projection_request=request, as_of=NOW,
        )

    short = project()
    assert short["available"] is False
    assert short["candles"] == []
    assert short["availability_state"] == short["reason"] == "SOR_HISTORY_TARGET_NOT_MET"
    assert short["available_timeframe_bars"] == short["available_minute_candles"] == 5
    assert short["required_timeframe_bars"] == 6
    assert short["calculation_only"] is True
    assert short["calculation_source"] == "BROKER_AL_CONFIRMED_M1"
    assert short["source_proof"] == "BROKER_ROWS_NOT_INDEPENDENT_MERGE_ATTESTATION"
    assert all(short[key] is False for key in
               ("execution_enabled", "queue_connected", "send_order_connected"))

    request["history_target_bars"] = 5
    exact = project()
    assert exact["available"] is True
    assert len(exact["candles"]) == 5
    del request["history_target_bars"]
    legacy = project()
    assert legacy["available"] is True
    assert len(legacy["candles"]) == 5
    for invalid_target in (True, 0, -1, 1.5, "6", None):
        request["history_target_bars"] = invalid_target
        invalid = project()
        assert invalid["available"] is False
        assert invalid["candles"] == []
        assert invalid["calculation_only"] is True
    request.update(warmup_bars=6, history_target_bars=5)
    invalid = project()
    assert invalid["available"] is False
    assert invalid["candles"] == []
    assert host._display_timeframe_candles == original


def test_sor_m3_target_900_blocks_without_direct_al_history():
    host = DisplayOnlyMarketDataHostTest()._host()
    previous = datetime(2026, 10, 19, 10, 0, tzinfo=SEOUL_TIMEZONE)
    current = datetime(2026, 10, 20, 9, 0, tzinfo=SEOUL_TIMEZONE)
    times = ([previous + timedelta(minutes=i) for i in range(320)]
             + [current + timedelta(minutes=i) for i in range(380)])
    rows = [_opt10080_row(when.strftime("%Y%m%d%H%M%S"), 100 + i)
            for i, when in enumerate(times)]
    as_of = datetime(2026, 10, 20, 15, 20, tzinfo=SEOUL_TIMEZONE)
    with patch("gui_market_data_host.load_stock_library_snapshot",
               return_value=DisplayOnlyMarketDataHostTest._snapshot(True)), patch.object(
        MarketDataHost, "direct_timeframe_requirements", return_value={1: 700},
    ):
        installed = MarketDataHost.install_display_timeframe_candles(
            host, "005930", 1, rows, as_of=as_of,
            market_data_identity="005930_AL", market_source="INTEGRATED",
            display_only=True, connection_epoch=1, login_session_id="SESSION-1",
        )
    assert installed["ok"] is True, installed
    assert installed["stored_count"] == 700
    host.kiwoom_api.request_minute_candles_display_only_integrated = (
        lambda *_args, **_kwargs: pytest.fail("Unexpected broker TR")
    )
    result = MarketDataHost.project_broker_sor_m1_calculation(
        host, stock_code="005930", rules={"bar": {"bar_minutes": 3}},
        projection_request={"projection": "COMPLETED_TIMEFRAME",
                            "warmup_bars": 1, "history_target_bars": 900},
        as_of=as_of,
        session_windows=({"name": "REGULAR", "start_time": "09:00",
                          "end_time": "15:20"},),
    )
    assert result["available"] is False
    assert result["candles"] == []
    assert result["availability_state"] == result["reason"] == "SOR_DIRECT_HISTORY_UNAVAILABLE"
    assert result["calculation_only"] is True
    assert result["queue_connected"] is result["send_order_connected"] is False


@pytest.mark.parametrize("target", (900, 5000))
def test_sor_m3_target_uses_direct_completed_bars_only(target):
    cache = Path(__file__).resolve().parents[1] / "runtime/signal_validation_v2_history_cache"
    as_of = datetime(2026, 10, 21, 15, 20, tzinfo=SEOUL_TIMEZONE)
    host = DisplayOnlyMarketDataHostTest()._host()
    host._candle_standby_timeframe_requirements_by_stock = {"005930": {1: 700, 3: target}}
    raw_keys = tuple(_opt10080_row("20261021090000", 100))

    with patch("gui_market_data_host.load_stock_library_snapshot",
               return_value=DisplayOnlyMarketDataHostTest._snapshot(True)):
        for minutes in (3, 1):
            path = cache / f"005930_AL_{minutes}_5900.json"
            entry = json.loads(path.read_text(encoding="utf-8"))
            marker = json.loads(path.with_name(path.name + ".market-source.json").read_text(encoding="utf-8"))
            assert (entry["market_data_identity"], entry["market_source"],
                    entry["timeframe_minutes"]) == ("005930_AL", "INTEGRATED", minutes)
            assert (marker["canonical_stock_code"], marker["market_data_identity"],
                    marker["market_source"]) == ("005930", "005930_AL", "INTEGRATED")
            content = json.dumps(entry["candles"], ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"), allow_nan=False).encode("utf-8")
            assert sha256(content).hexdigest() == marker["candle_content_sha256"]
            raw = [dict(zip(raw_keys, (row["time"], row["open"], row["high"],
                                       row["low"], row["close"], row["volume"])))
                   for row in entry["candles"]]
            installed = MarketDataHost.install_display_timeframe_candles(
                host, "005930", minutes, raw, as_of=as_of,
                market_data_identity="005930_AL", market_source="INTEGRATED",
                display_only=True, connection_epoch=1, login_session_id="SESSION-1",
            )
            assert installed["ok"] is True, installed

        direct_prior = [row for row in host._display_timeframe_candles[("005930", 3)]
                        if row["trade_date"] < "2026-10-21"]
        assert len(direct_prior) > 700
        assert len(host._display_timeframe_candles[("005930", 1)]) == 700
        sessions = (
            {"name": "NXT_PRE", "start_time": "08:00", "end_time": "08:50"},
            {"name": "KRX", "start_time": "09:00", "end_time": "15:30"},
            {"name": "NXT_AFTER", "start_time": "15:40", "end_time": "20:00"},
        )
        request = {"projection": "COMPLETED_TIMEFRAME", "warmup_bars": 1,
                   "history_target_bars": target}
        def calculate():
            return MarketDataHost.project_routine_candles(
                host, stock_code="005930", rules={"bar": {"bar_minutes": 3}},
                projection_request=request, as_of=as_of, session_windows=sessions,
                consumer_scope="SOR_CALCULATION_ONLY",
            )

        production_preview = calculate()
        mock_preview = calculate()
        direct_key = ("005930", 3)
        original_direct = host._display_timeframe_candles[direct_key]
        host._display_timeframe_candles[direct_key] = (
            {**original_direct[0], "market_data_identity": "005930"},
            *original_direct[1:],
        )
        wrong_direct_source = calculate()
        host._display_timeframe_candles[direct_key] = original_direct
        minute_key = ("005930", 1)
        original_minutes = host._display_timeframe_candles[minute_key]
        host._display_timeframe_candles[minute_key] = tuple(
            row for row in original_minutes
            if row["bar_time"] != "2026-10-21T08:00:00+09:00"
        )
        missing_open_minute = calculate()
        host._display_timeframe_candles[minute_key] = original_minutes
    for result in (production_preview, mock_preview):
        assert result["available"] is True, (result.get("availability_state"),
                                               result.get("available_timeframe_bars"))
        assert len(result["candles"]) == target
        assert result["candles"] == list(original_direct[-target:])
        assert result["market_data_identity"] == "005930_AL"
        assert result["calculation_only"] is True
        assert result["queue_connected"] is result["send_order_connected"] is False
    assert production_preview["candles"] == mock_preview["candles"]
    assert wrong_direct_source["availability_state"] == "SOR_DIRECT_HISTORY_PROVENANCE_INVALID"
    assert missing_open_minute["available"] is True
    assert missing_open_minute["candles"] == production_preview["candles"]


def test_v5_and_market_host_agree_on_same_broker_m1_ohlcv():
    """Compare the actual V5 snapshot projector to the MarketDataHost source."""
    from routines.지표추종매매.routine_validation_contract import ValidationStockRef
    from routines.지표추종매매.routine_validation_historical import ValidationHistoricalSnapshot
    from routines.지표추종매매.routine_validation_replay import project_validation_candles

    stock = ValidationStockRef("005930", "Test")
    raw_rows = [_opt10080_row(f"20261020090{i}00", 100 + i) for i in range(5)]
    snapshot = ValidationHistoricalSnapshot(
        stock=stock, timeframe_minutes=1, timeframe_key="M1",
        requested_count=len(raw_rows), request_id="offline-broker-al-m1",
        rows=raw_rows, market_data_identity="005930_AL",
        market_source="INTEGRATED", request_as_of=NOW.isoformat(),
    )
    v5, dropped = project_validation_candles(snapshot, as_of=NOW)
    market = _project(_installed_host(), 1)
    assert market["available"] is True
    assert dropped == 0
    assert len(v5) == len(market["candles"])
    for before, after in zip(v5, market["candles"]):
        from candle_timeframe_aggregation import candle_market_datetime
        assert candle_market_datetime(before) == candle_market_datetime(after)
        for field in ("open", "high", "low", "close", "volume"):
            assert before[field] == after[field]


def test_actual_buy_filter_matches_v5_from_same_broker_rows():
    """Run the existing filter evaluator, not a fake BUY/SELL result."""
    from routines.지표추종매매 import routine
    from routines.지표추종매매.routine_validation_contract import ValidationStockRef
    from routines.지표추종매매.routine_validation_historical import ValidationHistoricalSnapshot
    from routines.지표추종매매.routine_validation_replay import project_validation_candles
    from tests.test_sor_bundle_d_calculation import _input

    _market, _api, _as_of, _batch, _view, rules = _input()
    rules["bar_minutes"] = 1
    raw_rows = [_opt10080_row(f"20261020090{i}00", 100 + i) for i in range(5)]
    v5_snapshot = ValidationHistoricalSnapshot(
        stock=ValidationStockRef("005930", "Test"), timeframe_minutes=1,
        timeframe_key="M1", requested_count=5,
        request_id="same-broker-al-m1",
        rows=raw_rows, market_data_identity="005930_AL",
        market_source="INTEGRATED", request_as_of=NOW.isoformat(),
    )
    v5_rows, _dropped = project_validation_candles(v5_snapshot, as_of=NOW)
    live = _project(_installed_host(), 1)
    assert live["available"] is True

    from_v5 = routine.evaluate_signal_selection(
        v5_rows, rules, {"candles": v5_rows}
    )
    from_live = routine.evaluate_signal_selection(
        live["candles"], rules, {"candles": live["candles"]}
    )
    assert from_v5["signal"] == from_live["signal"] == "BUY"
    assert from_v5.get("signal_source_index") == from_live.get("signal_source_index")
    assert from_v5.get("signal_activation_index") == from_live.get("signal_activation_index")


def test_higher_timeframe_blocks_without_direct_sor_source():
    host = _installed_host()
    five = _project(host, 5)
    assert five["available"] is False
    assert five["candles"] == []
    assert five["availability_state"] == "SOR_DIRECT_HISTORY_UNAVAILABLE"


def test_warmup_failure_does_not_fallback_to_krx():
    host = _installed_host()
    result = _project(host, 5, count=2)
    assert result["available"] is False
    assert result["candles"] == []
    assert result["market_source"] == "INTEGRATED"


def test_sor_forming_bar_rejects_unverified_krx_tick():
    host = _installed_host()
    result = _project(host, 5, projection="FORMING_BASE_BAR")
    assert result["available"] is False
    assert result["availability_state"] == "SOR_FORMING_SOURCE_NOT_VERIFIED"
    assert result["candles"] == []


def _forming_host(*, metadata=True, request_time=None, received_time=None,
                  forming_price=110):
    host = _installed_host()
    start = request_time or (NOW + timedelta(seconds=10))
    received = received_time or (NOW + timedelta(seconds=11))
    rows = [
        _opt10080_row("20261020090500", forming_price),
        _opt10080_row("20261020090400", 104),
    ]
    extra = (
        {"request_as_of": start.isoformat(),
         "received_at": received.isoformat()}
        if metadata else {}
    )
    with patch(
        "gui_market_data_host.load_stock_library_snapshot",
        return_value=DisplayOnlyMarketDataHostTest._snapshot(True),
    ), patch.object(
        MarketDataHost, "direct_timeframe_requirements", return_value={1: 5},
    ):
        outcome = MarketDataHost.install_display_timeframe_candles(
            host, "005930", 1, rows, as_of=NOW,
            market_data_identity="005930_AL", market_source="INTEGRATED",
            display_only=True, connection_epoch=1, login_session_id="SESSION-1",
            **extra,
        )
    assert outcome["ok"] is True, outcome
    return host


def test_accepted_broker_al_m1_records_once_without_projection_or_tr():
    host = _installed_host()
    sampler = Mock()
    host._sor_tick_evidence = sampler
    host.market_data_observed = Mock()
    rows = [_opt10080_row("20261020090500", 110),
            _opt10080_row("20261020090400", 104)]
    with patch(
        "gui_market_data_host.load_stock_library_snapshot",
        return_value=DisplayOnlyMarketDataHostTest._snapshot(True),
    ), patch.object(
        MarketDataHost, "direct_timeframe_requirements", return_value={1: 5},
    ), patch.object(
        MarketDataHost, "project_broker_sor_m1_calculation",
        side_effect=AssertionError("projection during capture"),
    ):
        result = MarketDataHost.install_display_timeframe_candles(
            host, "005930", 1, rows, as_of=NOW,
            market_data_identity="005930_AL", market_source="INTEGRATED",
            display_only=True, connection_epoch=1, login_session_id="SESSION-1",
            request_as_of=(NOW + timedelta(seconds=10)).isoformat(),
            received_at=(NOW + timedelta(seconds=11)).isoformat(),
        )
    assert result["ok"] is True
    sampler.capture_accepted_al_m1.assert_called_once()
    args, kwargs = sampler.capture_accepted_al_m1.call_args
    assert args[0] == "005930"
    assert args[1] == host._sor_forming_m1_by_stock["005930"]["candle"]
    assert kwargs == {
        "connection_epoch": 1, "login_session_id": "SESSION-1",
        "completed_count": result["stored_count"],
    }
    assert host.market_data_observed.emit.call_count == 1
    assert host.market_data_observed.emit.call_args.args[0]["source"] == "SOR_AL_M1_SNAPSHOT_ACCEPTED"


@pytest.mark.parametrize("case", [
    "wrong_session", "wrong_interval", "stale_received", "no_forming", "no_completed",
])
def test_al_m1_telemetry_skips_unaccepted_or_missing_rows(case):
    host = (DisplayOnlyMarketDataHostTest()._host() if case == "no_completed"
            else _installed_host())
    sampler = Mock()
    host._sor_tick_evidence = sampler
    rows = ([_opt10080_row("20261020090500", 110)]
            if case == "no_completed" else
            [_opt10080_row("20261020090400", 104)]
            if case == "no_forming" else
            [_opt10080_row("20261020090500", 110),
             _opt10080_row("20261020090400", 104)])
    with patch(
        "gui_market_data_host.load_stock_library_snapshot",
        return_value=DisplayOnlyMarketDataHostTest._snapshot(True),
    ), patch.object(
        MarketDataHost, "direct_timeframe_requirements", return_value={1: 5},
    ):
        MarketDataHost.install_display_timeframe_candles(
            host, "005930", 5 if case == "wrong_interval" else 1,
            rows, as_of=NOW, market_data_identity="005930_AL",
            market_source="INTEGRATED", display_only=True,
            connection_epoch=2 if case == "wrong_session" else 1,
            login_session_id="SESSION-1",
            request_as_of=(NOW + timedelta(seconds=10)).isoformat(),
            received_at=(NOW + timedelta(minutes=2) if case == "stale_received"
                         else NOW + timedelta(seconds=11)).isoformat(),
        )
    sampler.capture_accepted_al_m1.assert_not_called()


def test_al_m1_telemetry_failure_preserves_cache_and_observer():
    host = _installed_host()
    sampler = Mock()
    sampler.capture_accepted_al_m1.side_effect = OSError("telemetry failed")
    host._sor_tick_evidence = sampler
    host.market_data_observed = Mock()
    with patch(
        "gui_market_data_host.load_stock_library_snapshot",
        return_value=DisplayOnlyMarketDataHostTest._snapshot(True),
    ), patch.object(
        MarketDataHost, "direct_timeframe_requirements", return_value={1: 5},
    ):
        result = MarketDataHost.install_display_timeframe_candles(
            host, "005930", 1,
            [_opt10080_row("20261020090500", 110)], as_of=NOW,
            market_data_identity="005930_AL", market_source="INTEGRATED",
            display_only=True, connection_epoch=1, login_session_id="SESSION-1",
            request_as_of=(NOW + timedelta(seconds=10)).isoformat(),
            received_at=(NOW + timedelta(seconds=11)).isoformat(),
        )
    assert result["ok"] is True
    assert host._sor_forming_m1_by_stock["005930"]["candle"]["close"] == 111
    sampler.note_capture_failure.assert_called_once()
    assert host.market_data_observed.emit.call_count == 1


@pytest.mark.parametrize("minutes", [1, 5])
def test_broker_forming_m1_snapshot_feeds_existing_base_projector(minutes):
    host = _forming_host()
    before = deepcopy(host._display_timeframe_candles[("005930", 1)])
    result = _project(host, minutes, projection="FORMING_BASE_BAR")
    assert result["available"] is True, result
    assert result["calculation_only"] is True
    assert result["send_order_connected"] is False
    assert result["forming_included"] is True
    assert result["forming_snapshot_source"] == "BROKER_AL_TR_FORMING_M1"
    assert result["forming_snapshot_received_at"]
    assert result["freshness"]["as_of"] == result["forming_snapshot_received_at"]
    assert result["candles"][-1]["is_complete"] is False
    assert result["candles"][-1]["close"] == 111
    assert host._display_timeframe_candles[("005930", 1)] == before
    assert all(candle["is_complete"] for candle in before)
    assert len(before) == 5


def test_forming_receipt_binds_broker_rows_session_and_ohlcv_without_new_tr():
    host = _forming_host()
    with patch.object(host.kiwoom_api, "current_realtime_shadow_bar",
                      side_effect=AssertionError("no broker request")):
        first = _project(host, 1, projection="FORMING_BASE_BAR")
        again = _project(host, 1, projection="FORMING_BASE_BAR")
    receipt = first["sor_source_receipt"]
    assert receipt == again["sor_source_receipt"]
    assert receipt["stock_code"] == "005930"
    assert receipt["timeframe_key"] == receipt["source_base_timeframe_key"] == "M1"
    assert receipt["source_as_of"] == NOW.isoformat()
    assert receipt["received_at"] == first["forming_snapshot_received_at"]
    assert receipt["connection_epoch"] == 1
    assert len(receipt["source_snapshot_key"]) == len(receipt["session_sha256"]) == 64
    assert "SESSION-1" not in str(receipt)
    assert receipt["independent_merge_verified"] is False
    assert all(first[key] is False for key in
               ("execution_enabled", "queue_connected", "send_order_connected"))
    changed = _project(_forming_host(forming_price=111), 1,
                       projection="FORMING_BASE_BAR")
    assert changed["sor_source_receipt"]["source_snapshot_key"] != receipt["source_snapshot_key"]
    completed = _project(host, 1, projection="COMPLETED_TIMEFRAME")
    assert "sor_source_receipt" not in completed
    host.kiwoom_api.broker_session_snapshot = lambda: type("Session", (), {
        "connected": True, "connection_epoch": 2, "login_session_id": "SESSION-2"})()
    stale = _project(host, 1, projection="FORMING_BASE_BAR")
    assert stale["available"] is False
    assert "sor_source_receipt" not in stale


def test_v5_detached_live_m1_view_carries_same_source_receipt():
    from tests.test_indicator_follow_signal_validation_v5_al_single_source import (
        _registered_m1_calculation_case,
    )

    flow, session, pool, _market, _ = _registered_m1_calculation_case()
    market = _forming_host()
    market.project_broker_sor_m1_calculation = lambda **kwargs: (
        MarketDataHost.project_broker_sor_m1_calculation(market, **kwargs)
    )
    market._sor_m1_required_by_stock = {"005930": 5}
    flow.parent().existing_market_data_host = lambda: market
    flow._now_factory = lambda: NOW + timedelta(seconds=30)
    frozen = flow._sor_m1_calculation_snapshot(session, pool, live_forming=True)
    selected = flow._validation_pool_for_session(session, pool, target_count=5)
    view = flow._sor_m1_calculation_view(session, pool, selected, frozen)
    assert view["sor_source_receipt"] == frozen[2]["sor_source_receipt"]
    assert view["sor_source_receipt"] is not frozen[2]["sor_source_receipt"]
    assert view["sor_source_receipt"]["source_base_timeframe_key"] == "M1"


def test_forming_source_missing_or_time_skew_blocks_without_krx_fallback():
    assert _project(_installed_host(), 5, projection="FORMING_BASE_BAR")[
        "availability_state"
    ] == "SOR_FORMING_SOURCE_NOT_VERIFIED"
    for host in (
        _forming_host(metadata=False),
        _forming_host(
            request_time=NOW + timedelta(seconds=10),
            received_time=NOW + timedelta(minutes=1, seconds=1),
        ),
        _forming_host(
            request_time=NOW + timedelta(seconds=20),
            received_time=NOW + timedelta(seconds=10),
        ),
    ):
        bad = _project(host, 5, projection="FORMING_BASE_BAR")
        assert bad["available"] is False
        assert bad["candles"] == []
        assert bad["availability_state"] == "SOR_FORMING_SOURCE_NOT_VERIFIED"


def test_forming_snapshot_does_not_leak_after_new_response_without_evidence():
    host = _forming_host()
    assert _project(host, 1, projection="FORMING_BASE_BAR")["available"] is True
    with patch(
        "gui_market_data_host.load_stock_library_snapshot",
        return_value=DisplayOnlyMarketDataHostTest._snapshot(True),
    ), patch.object(
        MarketDataHost, "direct_timeframe_requirements", return_value={1: 5},
    ):
        outcome = MarketDataHost.install_display_timeframe_candles(
            host, "005930", 1, [_opt10080_row("20261020090400", 104)],
            as_of=NOW, market_data_identity="005930_AL",
            market_source="INTEGRATED", display_only=True,
            connection_epoch=1, login_session_id="SESSION-1",
        )
    assert outcome["ok"] is True
    assert _project(host, 1, projection="FORMING_BASE_BAR")[
        "availability_state"
    ] == "SOR_FORMING_SOURCE_NOT_VERIFIED"


def test_existing_display_tr_callback_publishes_snapshot_without_extra_request():
    from auto_candle_refresh import request_display_timeframe_candles_for_stock

    host = _installed_host()
    issued = []
    api = host.kiwoom_api

    def broker(code, _name, *, interval, count, callback, nxt_available):
        issued.append((code, interval, count, nxt_available))
        callback({
            "ok": True, "code": code, "interval": interval,
            "rows": [_opt10080_row("20261020090500", 110)],
            "market_data_identity": f"{code}_AL",
            "market_source": "INTEGRATED", "display_only": True,
            "connection_epoch": 1, "login_session_id": "SESSION-1",
            "request_as_of": (NOW + timedelta(seconds=10)).isoformat(),
            "received_at": (NOW + timedelta(seconds=11)).isoformat(),
        })
        return {"ok": True}

    api.request_minute_candles_display_only_integrated = broker
    host.install_display_timeframe_candles = (
        lambda *args, **kwargs:
        MarketDataHost.install_display_timeframe_candles(host, *args, **kwargs)
    )
    seen = []
    with patch(
        "gui_market_data_host.load_stock_library_snapshot",
        return_value=DisplayOnlyMarketDataHostTest._snapshot(True),
    ):
        started = request_display_timeframe_candles_for_stock(
            host, "005930", "Test",
            operation_cycle_minute_key="20261020090500",
            timeframe_minutes=1, count=2, nxt_available=True,
            on_terminal=seen.append,
        )
    assert started["ok"] is True
    assert seen[0]["ok"] is True
    assert issued == [("005930", 1, 2, True)]
    forming = _project(host, 5, projection="FORMING_BASE_BAR")
    assert forming["available"] is True
    assert forming["candles"][-1]["is_complete"] is False


def test_missing_or_stale_session_refuses_integrated_calculation():
    host = _installed_host()
    assert _project(host, 1)["available"] is True
    host.kiwoom_api.broker_session_snapshot = lambda: type(
        "Session", (), {
            "connected": True, "connection_epoch": 2,
            "login_session_id": "SESSION-2",
        }
    )()
    result = _project(host, 1)
    assert result["available"] is False
    assert result["availability_state"] == "SOR_M1_SOURCE_UNAVAILABLE"


def test_stale_current_minute_and_previous_day_cannot_be_traded():
    host = _installed_host()
    stale = _project(host, 1, as_of=NOW + timedelta(minutes=2))
    assert stale["available"] is False
    assert stale["availability_state"] == "SOR_M1_LAST_COMPLETED_MINUTE_MISSING"
    previous_day = _project(host, 1, as_of=NOW + timedelta(days=1))
    assert previous_day["available"] is False
    assert previous_day["availability_state"] == "SOR_M1_TRADE_DATE_STALE"


def test_missing_direct_m5_blocks_regardless_of_m1_gap():
    host = _installed_host()
    key = ("005930", 1)
    original = host._display_timeframe_candles[key]
    host._display_timeframe_candles[key] = tuple(
        row for row in original
        if not str(row["bar_time"]).startswith("2026-10-20T09:02:")
    )
    result = _project(host, 5)
    assert result["available"] is False
    assert result["availability_state"] == "SOR_DIRECT_HISTORY_UNAVAILABLE"
    assert result["reason"] == "SOR_DIRECT_HISTORY_UNAVAILABLE"


def test_legacy_production_scope_cannot_use_stored_krx():
    host = _installed_host()
    krx = [
        _calculation_minute("20261020090000", 400),
        _calculation_minute("20261020090100", 401),
    ]
    with patch("gui_market_data_host.load_candles", return_value=krx), patch(
        "gui_market_data_host.StockRepository",
    ) as repo, patch(
        "gui_market_data_host.load_stock_library_snapshot",
        return_value=DisplayOnlyMarketDataHostTest._snapshot(True),
    ):
        repo.return_value.resolve_stock_dir.return_value = Path("unused")
        result = _project(host, 1, scope="PRODUCTION")
    assert result["candles"] == []
    assert result["availability_state"] == "SOR_SOURCE_UNAVAILABLE"


def _probe_facts(supply, *, source="SOR"):
    from routine_main_facts import build_routine_main_facts_from_projection

    return build_routine_main_facts_from_projection({
        "stock_configs": {
            "005930": {
                "assigned_routine_instance_id": "isolated",
                "calculation_market_source": source,
            }
        },
        "stock_states": {
            "005930": {"trade_enabled": True, "status": "MONITORING"}
        },
        "orders": [], "fills": [], "positions": [], "signals": [],
        "market": {
            "raw_candles_by_code": {
                "005930": [_calculation_minute("20261020090000", 999)]
            },
            "candle_supply_by_code": (
                {"005930": supply} if supply is not None else {}
            ),
            "candle_session_windows": (
                {"name": "KRX", "start_time": "09:00:00",
                 "end_time": "15:30:00"},
            ),
            "reference_prices_by_code": {},
        },
    }).to_payload()


def _probe_sor(tmp_path, supply, *, projection="COMPLETED_TIMEFRAME", source="SOR"):
    from types import SimpleNamespace
    from unittest.mock import Mock

    import routine_signal_probe

    selected = Mock(return_value={"signal": "BUY", "signal_source_index": 2})
    routine = SimpleNamespace(
        ROUTINE_TYPE="isolated",
        market_bar_projection_request=lambda _rules: {
            "projection": projection
        },
        project_cycle_context=lambda **_facts: {"status": "resolved"},
        evaluate=Mock(side_effect=AssertionError("routine.evaluate must not trade")),
        evaluate_signal_selection=selected,
    )
    stock_dir = tmp_path / "005930_Test"
    rules = {"bar": {"bar_minutes": 1}}
    with patch.object(
        routine_signal_probe, "_load_instance_rules", return_value=rules
    ), patch.object(
        routine_signal_probe, "_maybe_enqueue_signal",
        side_effect=AssertionError("SOR preview must not enqueue")
    ), patch.object(
        routine_signal_probe, "project_candle_supply",
        side_effect=AssertionError("SOR source must not fall back to KRX")
    ), patch.object(
        routine_signal_probe, "read_reference_price", return_value=None
    ), patch.object(
        routine_signal_probe, "find_library_stock_by_code", return_value=None
    ), patch.object(
        routine_signal_probe, "observe_owner_failure_transition"
    ), patch.object(
        routine_signal_probe, "_append_log"
    ):
        result = routine_signal_probe.probe_routine_for_stock(
            routine, "isolated", stock_dir,
            NOW.isoformat(timespec="seconds"),
            decision_trace_observer=None,
            main_facts=_probe_facts(supply, source=source),
        )
    routine.evaluate.assert_not_called()
    return result, selected


def test_actual_routine_probe_reads_sor_and_never_enqueues(tmp_path):
    supply = _project(_installed_host(), 1)
    result, selected = _probe_sor(tmp_path, supply)
    assert result["signal"] == "SKIP"
    assert result["reason"] == "SOR_CALCULATION_ONLY_NO_ORDER"
    assert result["sor_preview_signal"] == "BUY"
    assert result["queue_connected"] is False
    assert result["send_order_connected"] is False
    assert selected.call_count == 1
    assert selected.call_args.args[0] == supply["candles"]


def test_legacy_krx_selection_cannot_run_production_probe(tmp_path):
    result, selected = _probe_sor(tmp_path, _project(_installed_host(), 1), source="KRX")
    assert result["signal"] == "SKIP"
    assert result["reason"] == "CALCULATION_MARKET_SOURCE_INVALID"
    selected.assert_not_called()


def test_routine_probe_consumes_al_forming_bar_but_never_enqueues(tmp_path):
    supply = _project(_forming_host(), 1, projection="FORMING_BASE_BAR")
    assert supply["available"] is True
    result, selected = _probe_sor(
        tmp_path, supply, projection="FORMING_BASE_BAR"
    )
    assert result["signal"] == "SKIP"
    assert result["reason"] == "SOR_CALCULATION_ONLY_NO_ORDER"
    assert result["sor_preview_signal"] == "BUY"
    assert result["queue_connected"] is False
    assert result["send_order_connected"] is False
    assert selected.call_count == 1
    assert selected.call_args.args[0][-1]["is_complete"] is False
    assert selected.call_args.args[0][-1]["close"] == 111


def test_sor_missing_or_forged_main_facts_never_falls_back(tmp_path):
    for invalid in (None, {"available": True, "candles": [
        _calculation_minute("20261020090000", 555)
    ]}):
        result, selected = _probe_sor(tmp_path, invalid)
        assert result["signal"] == "SKIP"
        assert result["reason"] == "SOR_CALCULATION_SOURCE_UNAVAILABLE"
        selected.assert_not_called()


def test_sor_unavailable_source_fails_closed_in_actual_probe(tmp_path):
    supply = dict(_project(_installed_host(), 1))
    supply.update(available=False, candles=[],
                  availability_state="SOR_FORMING_SOURCE_NOT_VERIFIED")
    result, selected = _probe_sor(tmp_path, supply)
    assert result["signal"] == "SKIP"
    assert result["reason"] == "SOR_FORMING_SOURCE_NOT_VERIFIED"
    selected.assert_not_called()


def test_opt_in_does_not_issue_broker_requests():
    host = _installed_host()
    def no_request(*_args, **_kwargs):
        raise AssertionError("Unexpected broker TR")
    host.kiwoom_api.request_minute_candles_display_only_integrated = no_request
    result = _project(host, 1)
    assert result["available"] is True
    assert result["queue_connected"] is False
