"""Offline regressions for the common native observation boundary."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
import pytest

from candle_timeframe_aggregation import SEOUL_TIMEZONE
from gui_market_data_host import MarketDataHost
from kiwoom_realtime_fids import NXT_REALTIME_EXECUTION_TYPE
from tests.test_display_only_integrated_history import _opt10080_row
from tests.test_kiwoom_realtime_registration import _api
from stock_market_source_plan import _build


def test_nx_registration_failed_return_rolls_back_without_active_snapshot():
    api = _api()
    original = api._control.dynamicCall

    def failed(signature, *args):
        if signature.startswith("SetRealReg"):
            api._control.calls.append((signature, args))
            return -1
        return original(signature, *args)

    api._control.dynamicCall = failed
    result = api.sync_nxt_display_registration(("005930",))
    assert result["ok"] is False
    assert result["active"] is False
    assert api.nxt_display_registration_snapshot().active is False
    assert any(call[0].startswith("SetRealRemove") for call in api._control.calls)


def test_explicit_v5_m3_lease_does_not_require_full_day_m1():
    host = SimpleNamespace(
        _v5_observation_leases={}, _v5_observation_timeframes={},
        _sor_standby_m1_required_by_stock={}, _sor_observation_m1_required_by_stock={},
        _sor_m1_required_by_stock={}, _sor_forming_m1_by_stock={},
        _v5_al_native_minutes={}, _v5_al_parity={}, _v5_watch_generation=0,
        _v5_native_last_attempt={},
        _al_realtime_target_codes=lambda: ("005930",),
        _v5_watch_inflight=False, _production_monitoring_stock_codes=(),
        kiwoom_api=SimpleNamespace(sync_v5_al_realtime_targets=lambda _: {"ok": True}),
        sync_monitoring_targets=lambda _: {"ok": True},
    )
    with patch.object(MarketDataHost, "_schedule_v5_observation_watch"):
        MarketDataHost.set_v5_observation_lease(
            host, "chart", "005930", timeframe_minutes=3,
        )
    assert host._v5_observation_timeframes["chart"] == 3
    assert "005930" not in host._sor_m1_required_by_stock


def test_explicit_period_lease_does_not_start_minute_history_bootstrap():
    host = SimpleNamespace(
        _v5_observation_leases={}, _v5_observation_timeframes={},
        _sor_standby_m1_required_by_stock={}, _sor_observation_m1_required_by_stock={},
        _sor_m1_required_by_stock={}, _sor_forming_m1_by_stock={},
        _v5_al_native_minutes={}, _v5_al_parity={}, _v5_native_last_attempt={},
        _v5_watch_generation=0, _v5_watch_inflight=False, _production_monitoring_stock_codes=(),
        _al_realtime_target_codes=lambda: ("005930",),
        kiwoom_api=SimpleNamespace(sync_v5_al_realtime_targets=lambda _: {"ok": True}),
        sync_monitoring_targets=lambda _: {"ok": True},
    )
    with patch.object(MarketDataHost, "_schedule_v5_observation_watch"):
        result = MarketDataHost.set_v5_observation_lease(
            host, "period", "005930", timeframe_minutes=0)
    assert result["ok"] is True
    assert host._sor_m1_required_by_stock == {}


def test_timeframe_change_rearms_periodic_watch_and_removed_lease_stays_stopped():
    scheduled = []
    host = SimpleNamespace(
        _v5_observation_leases={"chart": "005930"},
        _v5_observation_timeframes={"chart": 3},
        _sor_standby_m1_required_by_stock={}, _sor_observation_m1_required_by_stock={},
        _sor_m1_required_by_stock={}, _sor_forming_m1_by_stock={},
        _v5_al_native_minutes={}, _v5_al_parity={}, _v5_native_last_attempt={},
        _v5_watch_generation=7, _v5_watch_inflight=False, _shutting_down=False,
        _production_monitoring_stock_codes=(), _al_realtime_target_codes=lambda: ("005930",),
        kiwoom_api=SimpleNamespace(sync_v5_al_realtime_targets=lambda _: {"ok": True}),
        sync_monitoring_targets=lambda _: {"ok": True},
    )
    with patch("gui_market_data_host.QTimer.singleShot",
               side_effect=lambda delay, callback: scheduled.append((delay, callback))), \
            patch.object(MarketDataHost, "_refresh_v5_observation"):
        MarketDataHost.set_v5_observation_lease(host, "chart", "005930", timeframe_minutes=5)
        assert host._v5_watch_generation == 8
        periodic = [(delay, callback) for delay, callback in scheduled if delay > 0]
        assert len(periodic) == 1
        periodic[0][1]()
        assert len([delay for delay, _ in scheduled if delay > 0]) == 2
        MarketDataHost.set_v5_observation_lease(host, "chart", None)
        count = len(scheduled)
        scheduled[-1][1]()
        assert len(scheduled) == count


def test_native_selected_snapshot_is_bounded_and_rejects_completed_or_wrong_source():
    install = getattr(MarketDataHost, "install_native_observation_candle", None)
    assert callable(install)
    at = datetime(2026, 10, 26, 8, 2, 6, tzinfo=SEOUL_TIMEZONE)
    row = _opt10080_row("20261026080000", 101)
    plan = {"sessions": [{"name": "NXT_PRE", "source": "NXT",
             "market_data_identity": "005930_NX", "start_time": "08:00:00",
             "end_time": "08:50:00"}], "trading_date": "2026-10-26"}
    host = SimpleNamespace(
        _v5_observation_leases={"chart": "005930"},
        _v5_observation_timeframes={"chart": 3},
        _native_observation_forming_by_stock={},
        _realtime_shadow_session_identity=(7, "offline"),
        kiwoom_api=SimpleNamespace(broker_session_snapshot=lambda: SimpleNamespace(
            connected=True, connection_epoch=7, login_session_id="offline")),
        market_data_observed=SimpleNamespace(emit=lambda event: events.append(event)),
    )
    events = []
    with patch("gui_market_data_host.read_stock_market_source_plan", return_value=plan):
        accepted = install(host, "005930", 3, [row], source="NXT",
                           request_as_of=at - timedelta(seconds=9),
                           received_at=at, identity=(7, "offline"), request_id="r1", now=at)
        duplicate = install(host, "005930", 3, [row], source="NXT",
                            request_as_of=at - timedelta(seconds=9),
                            received_at=at, identity=(7, "offline"), request_id="r1", now=at)
        late = install(host, "005930", 3, [row], source="NXT",
                       request_as_of=at.replace(second=55),
                       received_at=at.replace(minute=3, second=1), identity=(7, "offline"),
                       request_id="r2", now=at.replace(minute=3, second=1))
        wrong = install(host, "005930", 3, [row], source="INTEGRATED",
                        request_as_of=at - timedelta(seconds=9),
                        received_at=at, identity=(7, "offline"), request_id="r3", now=at)
    assert accepted["ok"] is True, accepted
    assert accepted["request_count"] <= 3
    assert duplicate["ok"] is False
    assert late["ok"] is False
    assert wrong["ok"] is False
    assert len(events) == 1
    assert events[0]["source"] == "NATIVE_SELECTED_FORMING_ACCEPTED"
    assert events[0]["timeframe_minutes"] == 3
    assert events[0]["market_data_identity"] == "005930_NX"
    assert events[0]["forming_candle"]["close"] == 102


def test_native_receipt_order_and_age_do_not_replace_newer_forming_bar():
    at = datetime(2026, 10, 26, 8, 2, 12, tzinfo=SEOUL_TIMEZONE)
    plan = {"sessions": [{"name": "NXT_PRE", "source": "NXT",
             "market_data_identity": "005930_NX", "start_time": "08:00:00",
             "end_time": "08:50:00"}], "trading_date": "2026-10-26"}
    events = []
    session = [SimpleNamespace(connected=True, connection_epoch=7, login_session_id="offline")]
    host = SimpleNamespace(
        _v5_observation_leases={"chart": "005930", "long": "005930"},
        _v5_observation_timeframes={"chart": 3, "long": 240},
        _native_observation_forming_by_stock={},
        kiwoom_api=SimpleNamespace(broker_session_snapshot=lambda: session[0]),
        market_data_observed=SimpleNamespace(emit=events.append),
    )
    def install(interval, price, requested, received, request_id, now=at):
        return MarketDataHost.install_native_observation_candle(
            host, "005930", interval, [_opt10080_row("20261026080000", price)],
            source="NXT", request_as_of=requested, received_at=received,
            identity=(7, "offline"), request_id=request_id, now=now,
        )
    with patch("gui_market_data_host.read_stock_market_source_plan", return_value=plan):
        newer = install(3, 102, at.replace(second=0), at.replace(second=10), "new")
        older = install(3, 100, at.replace(minute=1, second=57),
                        at.replace(second=6), "old")
        duplicate = install(3, 102, at.replace(second=0), at.replace(second=10), "new")
        stale = install(240, 102, at.replace(minute=1, second=0),
                        at.replace(minute=1, second=1), "stale",
                        now=at.replace(minute=25))
        session[0] = SimpleNamespace(connected=True, connection_epoch=8,
                                     login_session_id="other")
        changed = install(3, 105, at.replace(second=1), at.replace(second=11), "changed")
        malformed = MarketDataHost.install_native_observation_candle(
            host, "005930", 3, [], source="NXT", request_as_of="bad",
            received_at=at, identity=(7, "offline"), request_id="bad", now=at,
        )
    assert newer["ok"] is True
    assert all(item["ok"] is False for item in (older, duplicate, stale, changed, malformed))
    assert host._native_observation_forming_by_stock[("005930", 3)]["candle"]["close"] == 103
    assert len(events) == 1


def test_native_response_keeps_direct_completed_separate_without_promoting_old_forming():
    at = datetime(2026, 10, 26, 8, 3, 6, tzinfo=SEOUL_TIMEZONE)
    plan = _build("005930", "Samsung", at.date(), at.isoformat(), True)
    events = []
    host = SimpleNamespace(
        _v5_observation_leases={"chart": "005930"},
        _v5_observation_timeframes={"chart": 3},
        _native_observation_forming_by_stock={},
        _native_observation_completed_by_stock={},
        kiwoom_api=SimpleNamespace(broker_session_snapshot=lambda: SimpleNamespace(
            connected=True, connection_epoch=7, login_session_id="offline")),
        market_data_observed=SimpleNamespace(emit=events.append),
    )
    with patch("gui_market_data_host.read_stock_market_source_plan", return_value=plan):
        first = MarketDataHost.install_native_observation_candle(
            host, "005930", 3, [_opt10080_row("20261026080000", 100),
                                   _opt10080_row("20261026080300", 103)],
            source="NXT", request_as_of=at.replace(second=1), received_at=at,
            identity=(7, "offline"), request_id="r1", now=at)
        assert first["ok"] is True
        assert [row["time"] for row in events[-1]["completed_candles"]] == ["20261026080000"]
        assert events[-1]["forming_candle"]["time"] == "20261026080300"
        later = at.replace(minute=6, second=4)
        second = MarketDataHost.install_native_observation_candle(
            host, "005930", 3, [_opt10080_row("20261026080600", 106)],
            source="NXT", request_as_of=later.replace(second=1), received_at=later,
            identity=(7, "offline"), request_id="r2", now=later)
    assert second["ok"] is True
    assert [row["time"] for row in host._native_observation_completed_by_stock[("005930", 3, "NXT")]["candles"]] == ["20261026080000"]
    assert events[-1]["completed_candles"] == []


def test_native_completed_delta_keeps_nx_and_al_histories_separate():
    plan = _build("005930", "Samsung", datetime(2026, 10, 26).date(),
                  "2026-10-26T08:00:00+09:00", True)
    host = SimpleNamespace(
        _v5_observation_leases={"chart": "005930"},
        _v5_observation_timeframes={"chart": 3},
        _native_observation_forming_by_stock={},
        _native_observation_completed_by_stock={},
        kiwoom_api=SimpleNamespace(broker_session_snapshot=lambda: SimpleNamespace(
            connected=True, connection_epoch=7, login_session_id="offline")),
        market_data_observed=SimpleNamespace(emit=lambda event: None),
    )
    with patch("gui_market_data_host.read_stock_market_source_plan", return_value=plan):
        for source, hour, prefix in (("NXT", 8, "08"), ("INTEGRATED", 9, "09")):
            at = datetime(2026, 10, 26, hour, 3, 6, tzinfo=SEOUL_TIMEZONE)
            result = MarketDataHost.install_native_observation_candle(
                host, "005930", 3,
                [_opt10080_row(f"20261026{prefix}0000", 100),
                 _opt10080_row(f"20261026{prefix}0300", 103)],
                source=source, request_as_of=at.replace(second=1),
                received_at=at, identity=(7, "offline"),
                request_id=f"{source}-1", now=at)
            assert result["ok"] is True, result
    caches = host._native_observation_completed_by_stock
    assert [row["time"] for row in caches[("005930", 3, "NXT")]["candles"]] == ["20261026080000"]
    assert [row["time"] for row in caches[("005930", 3, "INTEGRATED")]["candles"]] == ["20261026090000"]


def test_al_tick_extends_only_sequence_anchored_direct_m3_forming_bar():
    at = datetime(2026, 10, 26, 9, 1, 6, tzinfo=SEOUL_TIMEZONE)
    plan = _build("005930", "Samsung", at.date(), at.isoformat(), True)
    events = []
    api = SimpleNamespace(
        _v5_al_receive_sequence_by_code={"005930": 4},
        _v5_al_registration={"identity": (7, "offline"), "targets": ("005930",)},
        broker_session_snapshot=lambda: SimpleNamespace(
            connected=True, connection_epoch=7, login_session_id="offline"),
    )
    host = SimpleNamespace(
        _shutting_down=False, _v5_observation_leases={"chart": "005930"},
        _v5_observation_timeframes={"chart": 3}, _v5_watch_generation=2,
        _mini_chart_observation_leases={}, _sor_display_live_price_states={},
        _v5_al_native_minutes={}, _v5_al_parity={},
        _native_observation_forming_by_stock={},
        _native_observation_completed_by_stock={},
        kiwoom_api=api, market_data_observed=SimpleNamespace(emit=events.append),
        _current_display_session_identity=lambda: (7, "offline"),
    )
    host.native_selected_observation_snapshot = lambda *args: (
        MarketDataHost.native_selected_observation_snapshot(host, *args))
    row = _opt10080_row("20261026090000", 101)
    requested = at - timedelta(seconds=5)
    started = dict(ok=True, code="005930", rqname="m3", request_as_of=requested.isoformat(),
                   started_at=requested.isoformat(), market_source="INTEGRATED",
                   market_data_identity="005930_AL", display_only=True,
                   connection_epoch=7, login_session_id="offline")
    response = dict(started, request_id="m3", interval=3, rows=[row],
                    received_at=at.isoformat())
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return at + timedelta(seconds=20)
    with patch("gui_market_data_host.read_stock_market_source_plan", return_value=plan):
        result = MarketDataHost.install_native_observation_candle(
            host, "005930", 3, [row], source="INTEGRATED",
            request_as_of=requested, received_at=at, identity=(7, "offline"),
            request_id="m3", now=at, request_receipt=(started, response),
            tick_anchor_sequence=4)
        assert result["ok"] is True, result
        original = host.native_selected_observation_snapshot("chart", "005930", 3, "m3")
        tick = dict(stock_code="005930", broker_market_identity="005930_AL",
                    market_source="SOR", v5_only=True, connection_epoch=7,
                    login_session_id="offline", market_datetime=(at + timedelta(seconds=1)).isoformat(),
                    received_at=(at + timedelta(seconds=1)).isoformat(),
                    current_price=110, trade_volume_abs=3, cumulative_volume=103,
                    receive_sequence=5)
        api._v5_al_receive_sequence_by_code["005930"] = 5
        with patch("gui_market_data_host.datetime", Clock):
            MarketDataHost._on_v5_al_tick_received(host, tick)
        proof = host.native_selected_observation_snapshot("chart", "005930", 3, "m3")
        assert proof["forming_candle"]["close"] == 110
        assert proof["forming_candle"]["volume"] == original["forming_candle"]["volume"] + 3
        assert proof["request_receipt"] == original["request_receipt"]
        assert events[-1]["source"] == "NATIVE_SELECTED_TICK_ACCEPTED"
        with patch("gui_market_data_host.datetime", Clock):
            MarketDataHost._on_v5_al_tick_received(host, tick)
        assert host.native_selected_observation_snapshot("chart", "005930", 3, "m3") == proof
        assert len(events) == 2
        api._v5_al_receive_sequence_by_code["005930"] = 5
        row2 = dict(row)
        fields = list(row2)
        for field, value in zip(fields[1:], ("101", "110", "99", "110", "13")):
            row2[field] = value
        requested2 = at + timedelta(seconds=9)
        received2 = at + timedelta(seconds=10)
        started2 = dict(started, rqname="m3-overlap",
                        request_as_of=requested2.isoformat(),
                        started_at=requested2.isoformat())
        response2 = dict(started2, request_id="m3-overlap", interval=3,
                         rows=[row2], received_at=received2.isoformat())
        overlap = MarketDataHost.install_native_observation_candle(
            host, "005930", 3, [row2], source="INTEGRATED",
            request_as_of=requested2, received_at=received2,
            identity=(7, "offline"), request_id="m3-overlap", now=received2,
            request_receipt=(started2, response2), tick_anchor_sequence=5)
        assert overlap["ok"] is True, overlap
        assert host.native_selected_observation_snapshot("chart", "005930", 3,
                                                         "m3-overlap")["forming_candle"]["volume"] == 13
        next_tick = dict(tick, receive_sequence=6, current_price=120,
                         trade_volume_abs=2, cumulative_volume=105,
                         market_datetime=(at + timedelta(seconds=11)).isoformat(),
                         received_at=(at + timedelta(seconds=11)).isoformat())
        api._v5_al_receive_sequence_by_code["005930"] = 6
        with patch("gui_market_data_host.datetime", Clock):
            MarketDataHost._on_v5_al_tick_received(host, next_tick)
        overlap_proof = host.native_selected_observation_snapshot(
            "chart", "005930", 3, "m3-overlap")
        assert overlap_proof["forming_candle"]["volume"] == 15
        assert overlap_proof["request_receipt"] == (started2, response2)
        assert len(events) == 4
        # A normalized callback can be dropped after the API has assigned seq 7.
        # Its gap revokes the last derived decision while original TR evidence stays.
        api._v5_al_receive_sequence_by_code["005930"] = 7
        assert host.native_selected_observation_snapshot(
            "chart", "005930", 3, "m3-overlap") is None
        assert host._native_observation_receipt_by_stock[("005930", 3)][
            "request_receipt"] == (started2, response2)
        api._v5_al_receive_sequence_by_code["005930"] = 6
        original_registration = api._v5_al_registration
        api._v5_al_registration = {"identity": (7, "offline"), "targets": ("005930",)}
        assert host.native_selected_observation_snapshot(
            "chart", "005930", 3, "m3-overlap") is None
        api._v5_al_registration = original_registration
        api._v5_al_receive_sequence_by_code["005930"] = 7
        with patch("gui_market_data_host.datetime", Clock):
            MarketDataHost._on_v5_al_tick_received(host, dict(next_tick,
                receive_sequence=7, market_source="NXT", current_price=130,
                market_datetime=(at + timedelta(seconds=12)).isoformat(),
                received_at=(at + timedelta(seconds=12)).isoformat()))
        assert len(events) == 4
        assert host.native_selected_observation_snapshot(
            "chart", "005930", 3, "m3-overlap") is None
        api._v5_al_receive_sequence_by_code["005930"] = 8
        with patch("gui_market_data_host.datetime", Clock):
            MarketDataHost._on_v5_al_tick_received(host, dict(tick, receive_sequence=8,
                current_price=120,
                market_datetime=(at + timedelta(seconds=13)).isoformat(),
                received_at=(at + timedelta(seconds=13)).isoformat()))
        assert len(events) == 4
        api._v5_al_receive_sequence_by_code["005930"] = 9
        with patch("gui_market_data_host.datetime", Clock):
            MarketDataHost._on_v5_al_tick_received(host, dict(tick, receive_sequence=9,
                current_price=130,
                market_datetime=(at + timedelta(seconds=14)).isoformat(),
                received_at=(at + timedelta(seconds=14)).isoformat()))
        assert len(events) == 4
        assert host._native_observation_forming_by_stock[("005930", 3)][
            "tick_anchor_sequence"] is None
        assert host.native_selected_observation_snapshot(
            "chart", "005930", 3, "m3-overlap") is None
        fresh_requested = at + timedelta(seconds=15)
        fresh_received = at + timedelta(seconds=16)
        fresh_started = dict(started, rqname="m3-reanchor",
                             request_as_of=fresh_requested.isoformat(),
                             started_at=fresh_requested.isoformat())
        fresh_response = dict(fresh_started, request_id="m3-reanchor", interval=3,
                              rows=[row2], received_at=fresh_received.isoformat())
        restored = MarketDataHost.install_native_observation_candle(
            host, "005930", 3, [row2], source="INTEGRATED",
            request_as_of=fresh_requested, received_at=fresh_received,
            identity=(7, "offline"), request_id="m3-reanchor", now=fresh_received,
            request_receipt=(fresh_started, fresh_response), tick_anchor_sequence=9)
        assert restored["ok"] is True
        assert host.native_selected_observation_snapshot(
            "chart", "005930", 3, "m3-reanchor")["tick_revision"] == 0
        api._v5_al_receive_sequence_by_code["005930"] = 10
        with patch("gui_market_data_host.datetime", Clock):
            MarketDataHost._on_v5_al_tick_received(host, dict(
                next_tick, receive_sequence=10, cumulative_volume=110,
                market_datetime=(at + timedelta(seconds=17)).isoformat(),
                received_at=(at + timedelta(seconds=17)).isoformat()))
        assert host.native_selected_observation_snapshot(
            "chart", "005930", 3, "m3-reanchor")["tick_revision"] == 10
        with patch("gui_market_data_host.datetime", Clock):
            MarketDataHost._on_v5_al_tick_received(host, dict(
                next_tick, receive_sequence=10, cumulative_volume=110,
                market_datetime=(at + timedelta(seconds=17)).isoformat(),
                received_at=(at + timedelta(seconds=17)).isoformat()))
        assert host.native_selected_observation_snapshot(
            "chart", "005930", 3, "m3-reanchor")["tick_revision"] == 10
        api._v5_al_receive_sequence_by_code["005930"] = 11
        with patch("gui_market_data_host.datetime", Clock):
            MarketDataHost._on_v5_al_tick_received(host, dict(
                next_tick, receive_sequence=11, cumulative_volume=111,
                market_datetime=(at + timedelta(seconds=18)).isoformat(),
                received_at=(at + timedelta(seconds=18)).isoformat()))
        assert host._native_observation_forming_by_stock[("005930", 3)][
            "tick_anchor_sequence"] is None
        assert host.native_selected_observation_snapshot(
            "chart", "005930", 3, "m3-reanchor") is None
        api.broker_session_snapshot = lambda: SimpleNamespace(
            connected=True, connection_epoch=8, login_session_id="reconnected")
        assert host.native_selected_observation_snapshot(
            "chart", "005930", 3, "m3-overlap") is None
        assert host._native_observation_completed_by_stock[("005930", 3, "INTEGRATED")]["candles"] == ()


def test_krx_native_selected_tick_requires_market_source_plan():
    at = datetime(2026, 10, 27, 16, 26, 10, tzinfo=SEOUL_TIMEZONE)
    tick_at = at + timedelta(seconds=1)
    events = []
    registration = SimpleNamespace(
        active=True, connection_epoch=7, login_session_id="offline",
        target_stock_codes=("005930",),
    )
    host = SimpleNamespace(
        _current_display_session_identity=lambda: (7, "offline"),
        _v5_observation_leases={"chart": "005930"},
        _v5_observation_timeframes={"chart": 3},
        _native_observation_forming_by_stock={},
        _native_observation_receipt_by_stock={},
        kiwoom_api=SimpleNamespace(
            _realtime_shadow_registration=registration,
            broker_session_snapshot=lambda: SimpleNamespace(
                connected=True, connection_epoch=7, login_session_id="offline")),
        market_data_observed=SimpleNamespace(emit=events.append),
    )
    tick = dict(stock_code="005930", broker_code_identity="005930",
                market_source="KRX", market_datetime=tick_at.isoformat(),
                received_at=tick_at.isoformat(), native_receive_sequence=5,
                current_price=110, trade_volume_abs=3, cumulative_volume=103)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return at + timedelta(seconds=2)

    with patch("gui_market_data_host.datetime", Clock), \
            patch("gui_market_data_host.read_stock_market_source_plan", return_value=None):
        MarketDataHost._extend_native_selected_al_forming(host, tick)
    assert host._native_observation_forming_by_stock == {}
    assert host._native_observation_receipt_by_stock == {}
    assert events == []

    candle = dict(time="20261027162400", native_bar_end=at.replace(minute=27).isoformat(),
                  high=105, low=99, close=101, volume=100)
    key = ("005930", 3)
    host._native_observation_forming_by_stock[key] = dict(
        candle=candle, session_identity=(7, "offline"), request_id="krx-m3",
        tick_registration=registration, tick_anchor_sequence=4,
        received_at=at - timedelta(seconds=1), tick_last_market_time=at,
        tick_last_sequence=4, tick_last_cumulative_volume=100,
    )
    host._native_observation_receipt_by_stock[key] = dict(
        session_identity=(7, "offline"), request_id="krx-m3",
        market_source="KRX", market_data_identity="005930",
        tr_forming_candle=dict(candle), completed_candles=[],
    )
    plan = _build("005930", "Samsung", at.date(), at.isoformat(), False)
    with patch("gui_market_data_host.datetime", Clock), \
            patch("gui_market_data_host.read_stock_market_source_plan", return_value=plan):
        MarketDataHost._extend_native_selected_al_forming(host, tick)
    assert host._native_observation_receipt_by_stock[key]["forming_candle"]["close"] == 110
    assert host._native_observation_receipt_by_stock[key]["tick_revision"] == 5
    assert [event["source"] for event in events] == ["NATIVE_SELECTED_TICK_ACCEPTED"]


@pytest.mark.parametrize("initial_counts", [{}, {"005930": 0}])
def test_first_al_tick_accepts_valid_zero_anchor_with_empty_or_explicit_counter(
        initial_counts):
    at = datetime(2026, 10, 26, 9, 1, 6, tzinfo=SEOUL_TIMEZONE)
    plan = _build("005930", "Samsung", at.date(), at.isoformat(), True)
    events = []
    registration = {"identity": (7, "offline"), "targets": ("005930",)}
    api = SimpleNamespace(
        _v5_al_receive_sequence_by_code=dict(initial_counts),
        _v5_al_registration=registration,
        broker_session_snapshot=lambda: SimpleNamespace(
            connected=True, connection_epoch=7, login_session_id="offline"),
        request_minute_candles_display_only_integrated=lambda **_: (
            pytest.fail("first tick requested historical candles")),
    )
    host = SimpleNamespace(
        _shutting_down=False, _v5_observation_leases={"chart": "005930"},
        _v5_observation_timeframes={"chart": 3}, _v5_watch_generation=2,
        _mini_chart_observation_leases={}, _sor_display_live_price_states={},
        _v5_al_native_minutes={}, _v5_al_parity={},
        _native_observation_forming_by_stock={},
        _native_observation_completed_by_stock={},
        kiwoom_api=api, market_data_observed=SimpleNamespace(emit=events.append),
        _current_display_session_identity=lambda: (7, "offline"),
    )
    host.native_selected_observation_snapshot = lambda *args: (
        MarketDataHost.native_selected_observation_snapshot(host, *args))
    requested = at - timedelta(seconds=5)
    row = _opt10080_row("20261026090000", 101)
    started = dict(ok=True, code="005930", rqname="zero-anchor",
                   request_as_of=requested.isoformat(),
                   started_at=requested.isoformat(), market_source="INTEGRATED",
                   market_data_identity="005930_AL", display_only=True,
                   connection_epoch=7, login_session_id="offline")
    response = dict(started, interval=3, request_id="zero-anchor", rows=[row],
                    received_at=at.isoformat())
    def install(anchor=0):
        return MarketDataHost.install_native_observation_candle(
            host, "005930", 3, [row], source="INTEGRATED",
            request_as_of=requested, received_at=at, identity=(7, "offline"),
            request_id="zero-anchor", now=at, request_receipt=(started, response),
            tick_anchor_sequence=anchor)
    with patch("gui_market_data_host.read_stock_market_source_plan", return_value=plan):
        assert install()["ok"] is True
        seed = host.native_selected_observation_snapshot(
            "chart", "005930", 3, "zero-anchor")
        assert seed["tick_revision"] == 0
        tick_at = at + timedelta(seconds=1)
        api._v5_al_receive_sequence_by_code["005930"] = 1
        tick = dict(stock_code="005930", broker_market_identity="005930_AL",
                    market_source="SOR", v5_only=True, connection_epoch=7,
                    login_session_id="offline", market_datetime=tick_at.isoformat(),
                    received_at=tick_at.isoformat(), current_price=110,
                    trade_volume_abs=2, cumulative_volume=2, receive_sequence=1)
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return tick_at + timedelta(seconds=1)
        with patch("gui_market_data_host.datetime", Clock):
            MarketDataHost._on_v5_al_tick_received(host, tick)
        current = host.native_selected_observation_snapshot(
            "chart", "005930", 3, "zero-anchor")
        assert current["tick_revision"] == 1
        assert current["forming_candle"]["close"] == 110
        assert current["forming_candle"]["volume"] == seed["forming_candle"]["volume"] + 2
        assert current["request_receipt"] == seed["request_receipt"]
        assert events[-1]["source"] == "NATIVE_SELECTED_TICK_ACCEPTED"
        for bad_registration, counts, anchor in (
                (None, dict(initial_counts), 0),
                ({"identity": (8, "other"), "targets": ("005930",)},
                 dict(initial_counts), 0),
                ({"identity": (7, "offline"), "targets": ()},
                 dict(initial_counts), 0),
                (registration, {"005930": 1}, 0),
                (registration, {"005930": -1}, -1),
                (registration, {"005930": "0"}, 0),
                (registration, {"005930": False}, 0),
                (registration, dict(initial_counts), "0")):
            api._v5_al_registration = bad_registration
            api._v5_al_receive_sequence_by_code = counts
            assert install(anchor)["reason_code"] == "NATIVE_OBSERVATION_TICK_ANCHOR_INVALID"


def test_accepted_nx_tick_schedules_only_one_native_refresh():
    scheduled = []
    at = datetime(2026, 10, 26, 8, 2, 6, tzinfo=SEOUL_TIMEZONE)
    host = SimpleNamespace(
        _realtime_shadow_session_identity=(7, "offline"),
        _nxt_display_live_price_states={},
        _v5_observation_leases={"chart": "005930"},
        _v5_observation_timeframes={"chart": 3},
        _v5_native_tick_refresh_scheduled=False,
        _display_observation_target_stock_codes=lambda: ("005930",),
        _observe_realtime_boundary=lambda *a, **k: None,
    )
    tick = {"canonical_stock_code": "005930", "broker_code_identity": "005930_NX",
            "market_source": "NXT", "source_real_type": NXT_REALTIME_EXECUTION_TYPE,
            "connection_epoch": 7, "login_session_id": "offline",
            "market_datetime": at.isoformat(), "current_price": 101,
            "receive_sequence": 1, "execution_time_raw": "080206",
            "received_at": at.isoformat()}
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return at
    plan = {"sessions": [{"name": "NXT_PRE", "source": "NXT",
             "market_data_identity": "005930_NX", "start_time": "08:00:00",
             "end_time": "08:50:00"}], "trading_date": "2026-10-26"}
    with patch("gui_market_data_host.datetime", Clock), \
            patch("gui_market_data_host.read_stock_market_source_plan", return_value=plan), \
            patch("gui_market_data_host.QTimer.singleShot",
                  side_effect=lambda _delay, callback: scheduled.append(callback)):
        assert MarketDataHost._process_nxt_display_tick(host, tick) is True
        assert MarketDataHost._process_nxt_display_tick(host,
               dict(tick, receive_sequence=2)) is True
    assert len(scheduled) == 1


@pytest.mark.parametrize("hour,minute,expected", [
    (8, 49, "NXT"), (8, 50, None), (9, 0, "INTEGRATED"),
    (9, 5, "INTEGRATED"), (15, 20, "INTEGRATED"),
    (15, 30, "INTEGRATED"), (15, 40, "INTEGRATED"),
])
def test_selected_m3_watch_uses_frozen_native_session_source(hour, minute, expected):
    at = datetime(2026, 10, 26, hour, minute, 10, tzinfo=SEOUL_TIMEZONE)
    plan = _build("005930", "Samsung", at.date(), at.isoformat(), True)
    calls = []
    def request(source):
        def start(code, name, **kwargs):
            calls.append((source, code, kwargs["interval"], kwargs["count"]))
            return {"ok": True}
        return start
    api = SimpleNamespace(
        sync_v5_al_realtime_targets=lambda _: {"ok": True},
        request_minute_candles_display_only_nxt=request("NXT"),
        request_minute_candles_display_only_integrated=request("INTEGRATED"),
    )
    host = SimpleNamespace(
        _v5_watch_inflight=False, _v5_watch_generation=1,
        _v5_observation_leases={"chart": "005930"},
        _v5_observation_timeframes={"chart": 3},
        _v5_native_last_attempt={}, _shutting_down=False,
        _current_display_session_identity=lambda: (7, "offline"),
        _al_realtime_target_codes=lambda: ("005930",),
        kiwoom_api=api,
    )
    with patch.object(MarketDataHost, "_nxt_eligible_targets", return_value=("005930",)), \
            patch("gui_market_data_host.read_stock_market_source_plan", return_value=plan):
        MarketDataHost._refresh_v5_observation(host, now=at)
    assert calls == ([] if expected is None else [(expected, "005930", 3, 3)])


def test_native_callback_binds_started_request_and_finishes_once_with_timeout():
    at = datetime(2026, 10, 26, 8, 2, 12, tzinfo=SEOUL_TIMEZONE)
    plan = _build("005930", "Samsung", at.date(), at.isoformat(), True)
    timers, callbacks, installed = [], [], []
    started = dict(ok=True, code="005930", interval=3, rqname="native-1",
                   request_as_of=at.replace(second=0).isoformat(),
                   market_data_identity="005930_NX", market_source="NXT",
                   display_only=True, connection_epoch=7, login_session_id="offline")
    def request(code, name, **kwargs):
        callbacks.append(kwargs["callback"])
        assert kwargs["count"] == 3 and kwargs["interval"] == 3
        return dict(started)
    api = SimpleNamespace(sync_v5_al_realtime_targets=lambda _: {"ok": True},
                          request_minute_candles_display_only_nxt=request)
    host = SimpleNamespace(
        _v5_watch_inflight=False, _v5_watch_generation=1,
        _v5_observation_leases={"chart": "005930"},
        _v5_observation_timeframes={"chart": 3}, _v5_native_last_attempt={},
        _shutting_down=False, _current_display_session_identity=lambda: (7, "offline"),
        _al_realtime_target_codes=lambda: ("005930",), kiwoom_api=api,
    )
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return at
    terminal = dict(started, request_id="native-1", received_at=at.isoformat(),
                    rows=[_opt10080_row("20261026080000", 100)])
    with patch.object(MarketDataHost, "_nxt_eligible_targets", return_value=("005930",)), \
            patch("gui_market_data_host.read_stock_market_source_plan", return_value=plan), \
            patch("gui_market_data_host.datetime", Clock), \
            patch("gui_market_data_host.QTimer.singleShot",
                  side_effect=lambda delay, cb: timers.append((delay, cb))), \
            patch.object(MarketDataHost, "install_native_observation_candle",
                         side_effect=lambda *a, **k: installed.append(k) or {"ok": True}):
        MarketDataHost._refresh_v5_observation(host, now=at)
        callbacks[0](dict(terminal, request_id="wrong"))
        assert installed == []
        callbacks[0](terminal)
        callbacks[0](terminal)
        assert len(installed) == 1
        assert installed[0]["request_id"] == "native-1"
        assert host._v5_watch_inflight is False
        assert any(delay == 15000 for delay, _ in timers)
        host._v5_watch_generation += 1
        callbacks[0](terminal)
        assert len(installed) == 1


def test_native_watch_timeout_unblocks_missing_callback():
    at = datetime(2026, 10, 26, 8, 2, 12, tzinfo=SEOUL_TIMEZONE)
    timers = []
    api = SimpleNamespace(sync_v5_al_realtime_targets=lambda _: {"ok": True},
        request_minute_candles_display_only_nxt=lambda code, name, **kwargs: {
            "ok": True, "rqname": "pending", "request_as_of": at.isoformat()})
    host = SimpleNamespace(_v5_watch_inflight=False, _v5_watch_generation=1,
        _v5_observation_leases={"chart": "005930"},
        _v5_observation_timeframes={"chart": 3}, _v5_native_last_attempt={},
        _shutting_down=False, _current_display_session_identity=lambda: (7, "offline"),
        _al_realtime_target_codes=lambda: ("005930",), kiwoom_api=api)
    plan = _build("005930", "Samsung", at.date(), at.isoformat(), True)
    with patch.object(MarketDataHost, "_nxt_eligible_targets", return_value=("005930",)), \
            patch("gui_market_data_host.read_stock_market_source_plan", return_value=plan), \
            patch("gui_market_data_host.QTimer.singleShot",
                  side_effect=lambda delay, cb: timers.append((delay, cb))):
        MarketDataHost._refresh_v5_observation(host, now=at)
        assert host._v5_watch_inflight is True
        timeout = next(cb for delay, cb in timers if delay == 15000)
        timeout()
        assert host._v5_watch_inflight is False


def test_delayed_next_request_reselects_source_at_dispatch():
    before = datetime(2026, 10, 26, 8, 49, 10, tzinfo=SEOUL_TIMEZONE)
    clock = [before]
    timers, calls, callbacks = [], [], []
    def request(source):
        def start(code, name, **kwargs):
            calls.append((source, code, kwargs["interval"], kwargs["count"]))
            callbacks.append(kwargs["callback"])
            return {"ok": True, "rqname": f"{code}-1",
                    "request_as_of": clock[0].isoformat()}
        return start
    api = SimpleNamespace(sync_v5_al_realtime_targets=lambda _: {"ok": True},
        request_minute_candles_display_only_nxt=request("NXT"),
        request_minute_candles_display_only_integrated=request("INTEGRATED"))
    host = SimpleNamespace(_v5_watch_inflight=False, _v5_watch_generation=1,
        _v5_observation_leases={"a": "005930", "b": "006400"},
        _v5_observation_timeframes={"a": 3, "b": 3}, _v5_native_last_attempt={},
        _shutting_down=False, _current_display_session_identity=lambda: (7, "offline"),
        _al_realtime_target_codes=lambda: ("005930", "006400"), kiwoom_api=api)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]
    with patch.object(MarketDataHost, "_nxt_eligible_targets",
                      return_value=("005930", "006400")), \
            patch("gui_market_data_host.read_stock_market_source_plan",
                  side_effect=lambda root, code, day: _build(code, "Name", day,
                                                               before.isoformat(), True)), \
            patch("gui_market_data_host.datetime", Clock), \
            patch("gui_market_data_host.QTimer.singleShot",
                  side_effect=lambda delay, cb: timers.append((delay, cb))):
        MarketDataHost._refresh_v5_observation(host, now=before)
        assert calls == [("NXT", "005930", 3, 3)]
        callbacks[0](None)
        clock[0] = before.replace(hour=9, minute=0)
        next_request = next(cb for delay, cb in timers if delay == 1100)
        next_request()
    assert calls[-1] == ("INTEGRATED", "006400", 3, 3)
