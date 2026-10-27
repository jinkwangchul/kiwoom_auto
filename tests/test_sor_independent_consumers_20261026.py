"""Offline selected AL current path owned by production and Mock requirements."""

from copy import deepcopy
from datetime import datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import patch
import time

import pytest

from PyQt5.QtCore import QObject, QCoreApplication

import gui_market_data_host as host_module
from candle_timeframe_aggregation import SEOUL_TIMEZONE
from gui_market_data_host import MarketDataHost
from tests.test_display_only_integrated_history import _opt10080_row


CODE = "005930"
SESSION = (7, "offline")
RULES = {"bar": {"bar_minutes": 3}}
REQUEST = {"projection": "FORMING_BASE_BAR", "warmup_bars": 1,
           "history_target_bars": 300}
PLAN = {"nxt_available": True, "trading_date": "2026-10-26", "sessions": [
    {"name": "KRX", "source": "SOR", "market_data_identity": "005930_AL",
     "start_time": "09:00:00", "end_time": "15:20:00"}]}


class Broker:
    def __init__(self, clock):
        self.clock = clock
        self.targets = ()
        self.sync_calls = []
        self.requests = []
        self.pending = []
        self.response_rows = None
        self._v5_al_receive_sequence_by_code = {}
        self._v5_al_registration = None

    def broker_session_snapshot(self):
        return SimpleNamespace(connected=True, connection_epoch=SESSION[0],
                               login_session_id=SESSION[1])

    def sync_realtime_monitoring_registration(self, codes):
        return {"ok": True, "snapshot": SimpleNamespace(
            active=True, connection_epoch=SESSION[0], login_session_id=SESSION[1],
            target_stock_codes=tuple(codes))}

    sync_realtime_shadow_targets = sync_realtime_monitoring_registration

    def sync_v5_al_realtime_targets(self, codes):
        codes = tuple(codes)
        self.sync_calls.append(codes)
        if codes != self.targets:
            self.targets = codes
            self._v5_al_registration = {"identity": SESSION, "targets": codes} if codes else None
            self._v5_al_receive_sequence_by_code.clear()
        return {"ok": True, "changed": codes != self.targets}

    def request_minute_candles_display_only_integrated(
            self, code, _name, *, interval, count, nxt_available, callback):
        self.requests.append((code, interval, count))
        request_at = self.clock[0] - timedelta(seconds=4)
        request_id = f"native-{len(self.requests)}"
        started = {"ok": True, "code": code, "rqname": request_id,
                   "market_source": "INTEGRATED", "market_data_identity": f"{code}_AL",
                   "display_only": True, "connection_epoch": SESSION[0],
                   "login_session_id": SESSION[1],
                   "request_as_of": request_at.isoformat()}
        self.pending.append((callback, started, interval))
        return started

    def finish(self):
        callback, started, interval = self.pending.pop(0)
        callback({**started, "interval": interval, "request_id": started["rqname"],
                   "rows": self.response_rows or [
                       _opt10080_row("20261026090000", 100),
                       _opt10080_row("20261026090300", 100)],
                  "received_at": self.clock[0].isoformat()})


def _host(monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    clock = [datetime(2026, 10, 26, 9, 4, 6, tzinfo=SEOUL_TIMEZONE)]
    broker = Broker(clock)
    monkeypatch.setattr(host_module, "read_stock_market_source_plan", lambda *_: PLAN)
    monkeypatch.setattr(host_module.QTimer, "singleShot", lambda *_: None)
    host = MarketDataHost(QObject(), broker, lambda _code: None)
    return host, broker, clock


def _requirement(frame=3):
    return {"stock_code": CODE, "rules": {"bar": {"bar_minutes": frame}},
            "projection_request": REQUEST, "calculation_market_source": "SOR"}


def _tick(host, broker, clock, sequence=1, price=150):
    clock[0] += timedelta(seconds=1)
    broker._v5_al_receive_sequence_by_code[CODE] = sequence
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]
    event = {"stock_code": CODE, "broker_market_identity": f"{CODE}_AL",
             "market_source": "SOR", "v5_only": True,
             "connection_epoch": SESSION[0], "login_session_id": SESSION[1],
             "market_datetime": clock[0].isoformat(),
             "received_at": clock[0].isoformat(), "current_price": price,
             "trade_volume_abs": 3, "cumulative_volume": 103,
             "receive_sequence": sequence}
    with patch.object(host_module, "datetime", Clock):
        host._on_v5_al_tick_received(event)


def test_no_v5_production_and_mock_share_native_current_and_survive_window_close(monkeypatch):
    host, broker, clock = _host(monkeypatch)
    assert host.sync_candle_standby_requirements([_requirement()])["ok"] is True
    assert host.sync_monitoring_targets((CODE,))["ok"] is True
    assert host.sync_candle_observation_targets([_requirement()])["ok"] is True
    assert host._v5_observation_leases == {}
    assert broker.targets == (CODE,)
    assert CODE not in host._sor_m1_required_by_stock
    host._refresh_v5_observation(now=clock[0])
    assert broker.requests == [(CODE, 3, 3)]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]
    with patch.object(host_module, "datetime", Clock):
        broker.finish()
    first = host.native_selected_current_snapshot(CODE, 3, consumer="PRODUCTION")
    assert first and first["forming_candle"]["close"] == 101
    assert host.native_selected_current_snapshot(CODE, 3, consumer="MOCK") == first
    assert host.native_selected_current_snapshot(CODE, 5, consumer="MOCK") is None
    _tick(host, broker, clock)
    current = host.native_selected_current_snapshot(CODE, 3, consumer="MOCK")
    assert current["forming_candle"]["close"] == 150
    assert current["forming_candle"]["volume"] == first["forming_candle"]["volume"] + 3
    assert current["request_receipt"] == first["request_receipt"]
    registration = broker._v5_al_registration
    assert host.set_v5_observation_lease("window", CODE, timeframe_minutes=3)["ok"] is True
    assert broker._v5_al_registration is registration
    assert host.native_selected_observation_snapshot("window", CODE, 3, first["request_id"])
    assert host.set_v5_observation_lease("window", None)["ok"] is True
    assert broker._v5_al_registration is registration
    assert host.native_selected_observation_snapshot("window", CODE, 3, first["request_id"]) is None
    assert host.native_selected_current_snapshot(CODE, 3, consumer="PRODUCTION") == current
    assert host.native_selected_current_snapshot(CODE, 3, consumer="MOCK") == current
    assert broker.targets == (CODE,)
    assert broker.requests == [(CODE, 3, 3)]
    host.sync_candle_standby_requirements([])
    host.sync_monitoring_targets((CODE,))
    assert broker._v5_al_registration is registration
    assert host.native_selected_current_snapshot(CODE, 3, consumer="PRODUCTION") is None
    assert host.native_selected_current_snapshot(CODE, 3, consumer="MOCK") == current
    host.sync_candle_observation_targets([])
    assert host.native_selected_current_snapshot(CODE, 3, consumer="MOCK") is None
    assert (CODE, 3) not in host._native_observation_forming_by_stock
    assert broker.targets == ()


def test_runtime_frame_and_identity_boundaries_fail_closed(monkeypatch):
    host, broker, clock = _host(monkeypatch)
    unknown = _requirement()
    unknown["calculation_market_source"] = "UNKNOWN"
    host.sync_candle_observation_targets([unknown])
    assert broker.targets == ()
    host.sync_candle_standby_requirements([_requirement()])
    host.sync_candle_observation_targets([_requirement()])
    host.sync_monitoring_targets((CODE,))
    host._refresh_v5_observation(now=clock[0])
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]
    with patch.object(host_module, "datetime", Clock):
        broker.finish()
    _tick(host, broker, clock)
    request_id = host._native_observation_receipt_by_stock[(CODE, 3)]["request_id"]
    assert host.native_selected_observation_snapshot("made-up", CODE, 3, request_id) is None
    assert host.native_selected_current_snapshot(CODE, 3, consumer="MOCK")
    broker._v5_al_receive_sequence_by_code[CODE] = 2
    assert host.native_selected_current_snapshot(CODE, 3, consumer="MOCK") is None
    broker._v5_al_receive_sequence_by_code[CODE] = 1
    previous_registration = broker._v5_al_registration
    broker._v5_al_registration = dict(previous_registration)
    assert host.native_selected_current_snapshot(CODE, 3, consumer="PRODUCTION") is None
    broker._v5_al_registration = previous_registration
    broker.broker_session_snapshot = lambda: SimpleNamespace(
        connected=True, connection_epoch=8, login_session_id="reconnected")
    assert host.native_selected_current_snapshot(CODE, 3, consumer="MOCK") is None
    broker.broker_session_snapshot = lambda: SimpleNamespace(
        connected=True, connection_epoch=SESSION[0], login_session_id=SESSION[1])
    host.sync_candle_standby_requirements([_requirement(5)])
    assert host.native_selected_current_snapshot(CODE, 3, consumer="PRODUCTION") is None
    assert host.native_selected_current_snapshot(CODE, 3, consumer="MOCK")
    krx = _requirement(5)
    krx["calculation_market_source"] = "KRX"
    host.sync_candle_observation_targets([_requirement(), krx])
    assert host.native_selected_current_snapshot(CODE, 5, consumer="MOCK") is None
    host.sync_candle_observation_targets([])
    assert (CODE, 3) not in host._native_observation_receipt_by_stock


def test_removed_requirement_rejects_pending_native_response(monkeypatch):
    host, broker, clock = _host(monkeypatch)
    host.sync_candle_standby_requirements([_requirement()])
    host.sync_monitoring_targets((CODE,))
    host._refresh_v5_observation(now=clock[0])
    assert broker.pending
    host.sync_candle_standby_requirements([])
    host.sync_monitoring_targets((CODE,))
    broker.finish()
    assert host.native_selected_current_snapshot(CODE, 3, consumer="PRODUCTION") is None
    assert (CODE, 3) not in host._native_observation_receipt_by_stock
    assert broker.targets == ()


def test_tr_only_current_proof_revoked_on_same_login_registration_replacement(monkeypatch):
    host, broker, clock = _host(monkeypatch)
    host.sync_candle_observation_targets([_requirement()])
    host._refresh_v5_observation(now=clock[0])
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]
    with patch.object(host_module, "datetime", Clock):
        broker.finish()
    old = host.native_selected_current_snapshot(CODE, 3, consumer="MOCK")
    assert old is not None and old["tick_revision"] == 0
    broker._v5_al_receive_sequence_by_code[CODE] = 1
    assert host.native_selected_current_snapshot(CODE, 3, consumer="MOCK") is None
    broker._v5_al_receive_sequence_by_code[CODE] = 0
    broker._v5_al_registration = {"identity": SESSION, "targets": (CODE, "000660")}
    assert host.native_selected_current_snapshot(CODE, 3, consumer="MOCK") is None
    clock[0] += timedelta(seconds=11)
    host._refresh_v5_observation(now=clock[0])
    with patch.object(host_module, "datetime", Clock):
        broker.finish()
    fresh = host.native_selected_current_snapshot(CODE, 3, consumer="MOCK")
    assert fresh is not None and fresh["request_id"] != old["request_id"]


def test_tr_only_current_proof_revoked_after_first_unadmitted_tick(monkeypatch):
    host, broker, clock = _host(monkeypatch)
    host.sync_candle_observation_targets([_requirement()])
    host._refresh_v5_observation(now=clock[0])
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]
    with patch.object(host_module, "datetime", Clock):
        broker.finish()
    assert host.native_selected_current_snapshot(CODE, 3, consumer="MOCK")
    _tick(host, broker, clock, sequence=2)
    assert host.native_selected_current_snapshot(CODE, 3, consumer="MOCK") is None


def test_mock_public_registration_only_declares_running_instance(monkeypatch):
    from mock_validation_host import MockValidationHost

    host, broker, _clock = _host(monkeypatch)
    document = {"session": {"stock_code": CODE},
                "reference_snapshot": {"calculation_market_source": "SOR",
                                       "routine_instances": [{
                                           "routine_instance_id": "RI-1",
                                           "rules_snapshot": RULES}]},
                "instance_execution": {"RI-1": {
                    "state": "RUNNING", "progression_allowed": True}}}
    mock = MockValidationHost.__new__(MockValidationHost)
    mock.repository = SimpleNamespace(
        current_session_ids=lambda: {CODE: "MV-1"},
        read_session=lambda _session: document)
    mock._registration_session_ids = {}
    mock._sor_stock_codes = frozenset()
    mock._candle_observation_updater = host.sync_candle_observation_targets
    mock.routine_adapter = SimpleNamespace(market_bar_projection_request=lambda _: REQUEST)
    mock.api = SimpleNamespace(sync_mock_orderbook_registration=lambda *_a, **_k:
                               {"ok": True, "snapshot": SimpleNamespace(active=True)})
    mock.market_store = SimpleNamespace(apply_registration_snapshot=lambda _: None)
    assert MockValidationHost.sync_registration(mock)["ok"] is True
    assert host._native_selected_targets(consumer="MOCK") == {(CODE, 3)}
    document["instance_execution"]["RI-1"]["progression_allowed"] = False
    MockValidationHost.sync_registration(mock)
    assert host._native_selected_targets(consumer="MOCK") == set()
    assert broker.targets == ()


def test_mock_current_projection_consumes_same_verified_tick_and_real_selector(
        tmp_path, monkeypatch):
    import mock_validation_candle_provider as provider
    import mock_validation_host as mock_module
    import mock_validation_indicator_follow_adapter as adapter
    from mock_validation_host import MockValidationHost

    host, broker, clock = _host(monkeypatch)
    monkeypatch.setattr(provider, "read_stock_market_source_plan", lambda *_: PLAN)
    monkeypatch.setattr(mock_module, "read_stock_market_source_plan", lambda *_: PLAN)
    host.sync_candle_observation_targets([_requirement()])
    assert host._v5_observation_leases == {}
    assert CODE not in host._sor_m1_required_by_stock
    history = [_opt10080_row(
        (datetime(2026, 10, day, 9, tzinfo=SEOUL_TIMEZONE)
         + timedelta(minutes=3 * index)).strftime("%Y%m%d%H%M%S"), 100)
        for day, count in ((21, 100), (22, 100), (23, 99))
        for index in range(count)]
    history.append(_opt10080_row("20261026090000", 100))
    assert host.install_display_timeframe_candles(
        CODE, 3, history, as_of=clock[0],
        market_data_identity=f"{CODE}_AL", market_source="INTEGRATED",
        display_only=True, connection_epoch=SESSION[0], login_session_id=SESSION[1],
    )["ok"] is True
    host._refresh_v5_observation(now=clock[0])
    broker.response_rows = [_opt10080_row("20261026090000", 150),
                            _opt10080_row("20261026090300", 100)]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]
    with patch.object(host_module, "datetime", Clock):
        broker.finish()
    def read_value(**kwargs):
        value = deepcopy(host.project_routine_candles(**kwargs))
        value["calculation_as_of"] = kwargs["as_of"].isoformat()
        return value
    mock = MockValidationHost(
        broker, project_root=tmp_path, candles_provider=lambda **_: None,
        operation_policy_provider=lambda: {},
        sor_calculation_value_reader=read_value,
        sor_native_current_reader=lambda code, frame:
            host.native_selected_current_snapshot(code, frame, consumer="MOCK"),
    )
    document = {"session": {"stock_code": CODE}}
    rules = {"bar": {"bar_minutes": 3}, "buy": {"delay_bar": 0,
             "groups": [{"enabled": True, "conditions": [{"enabled": True,
             "target": "CLOSE", "operator": ">=", "value": 150}]}]},
             "sell": {"filters": {}, "signals": {}}}
    direct = read_value(stock_code=CODE, rules=rules,
                        projection_request={"projection": "COMPLETED_TIMEFRAME",
                                            "warmup_bars": 1, "history_target_bars": 300},
                        as_of=clock[0], session_windows=(),
                        consumer_scope="SOR_CALCULATION_ONLY")
    assert direct["available"] is True, direct
    from mock_validation_candle_provider import project_mock_sor_calculation_snapshot
    projected = project_mock_sor_calculation_snapshot(
        direct, stock_code=CODE, rules=rules,
        projection_request={"projection": "COMPLETED_TIMEFRAME",
                            "warmup_bars": 1, "history_target_bars": 300}, as_of=clock[0],
        native_selected_current=True)
    assert projected["available"] is True, (projected, direct)
    assert project_mock_sor_calculation_snapshot(
        direct, stock_code=CODE, rules=rules,
        projection_request={"projection": "COMPLETED_TIMEFRAME",
                            "warmup_bars": 1, "history_target_bars": 300},
        as_of=clock[0])["available"] is False
    def supply():
        return mock._instance_candle_projection(
            document, instance_id="RI-1", rules=rules, settings={}, now=clock[0],
            projection_request={"projection": "FORMING_BASE_BAR", "warmup_bars": 1,
                                "history_target_bars": 300})
    first = supply()
    assert first["available"] is True, first
    assert first["candles"][-2]["bar_time"].startswith("2026-10-26T09:00")
    assert first["candles"][-2]["close"] == 151
    assert first["candles"][-1]["close"] == 101
    _tick(host, broker, clock)
    second = supply()
    assert second["available"] is True, second
    assert second["candles"][-1]["close"] == 150
    assert second["native_current_tick_revision"] == 1
    assert second["source_identity"] != first["source_identity"]
    selected = adapter._pure_signal_selection()(
        second["candles"], rules,
        {"candles": second["candles"], "cycle": {"status": "resolved",
         "holding_qty": 0}, "routine_instance_id": "RI-1", "code": CODE,
         "probe_only": True, "_indicator_follow_validation_current_price": 150},
        evaluator=mock.routine_adapter._evaluator,
        converter=adapter._signal_payload)
    assert selected["signal"] == "BUY", selected
    sealed = deepcopy(host._native_observation_receipt_by_stock[(CODE, 3)])
    host._native_observation_receipt_by_stock[(CODE, 3)]["completed_candles"][0]["close"] = 150
    assert supply()["available"] is False
    host._native_observation_receipt_by_stock[(CODE, 3)] = sealed
    broker._v5_al_receive_sequence_by_code[CODE] = 2
    assert supply()["available"] is False


def test_selected_m1_mock_current_uses_verified_native_tick_without_v5(tmp_path, monkeypatch):
    import mock_validation_candle_provider as provider
    import mock_validation_host as mock_module
    import mock_validation_indicator_follow_adapter as adapter
    from mock_validation_host import MockValidationHost

    host, broker, clock = _host(monkeypatch)
    monkeypatch.setattr(provider, "read_stock_market_source_plan", lambda *_: PLAN)
    monkeypatch.setattr(mock_module, "read_stock_market_source_plan", lambda *_: PLAN)
    host.sync_candle_observation_targets([_requirement(1)])
    assert host._v5_observation_leases == {}
    history = [_opt10080_row(
        (datetime(2026, 10, day, 9, tzinfo=SEOUL_TIMEZONE)
         + timedelta(minutes=index)).strftime("%Y%m%d%H%M%S"), 100)
        for day, count in ((21, 100), (22, 100), (23, 96))
        for index in range(count)]
    history += [_opt10080_row(f"2026102609{minute:02d}00", 100)
                for minute in range(4)]
    assert host.install_display_timeframe_candles(
        CODE, 1, history, as_of=clock[0], market_data_identity=f"{CODE}_AL",
        market_source="INTEGRATED", display_only=True,
        connection_epoch=SESSION[0], login_session_id=SESSION[1])["ok"] is True
    broker.response_rows = [_opt10080_row("20261026090300", 100),
                            _opt10080_row("20261026090400", 100)]
    host._refresh_v5_observation(now=clock[0])
    assert broker.requests == [(CODE, 1, 3)]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]
    with patch.object(host_module, "datetime", Clock):
        broker.finish()
    def read_value(**kwargs):
        value = deepcopy(host.project_routine_candles(**kwargs))
        value["calculation_as_of"] = kwargs["as_of"].isoformat()
        return value
    mock = MockValidationHost(
        broker, project_root=tmp_path, candles_provider=lambda **_: None,
        operation_policy_provider=lambda: {},
        sor_calculation_value_reader=read_value,
        sor_native_current_reader=lambda code, frame:
            host.native_selected_current_snapshot(code, frame, consumer="MOCK"))
    rules = {"bar": {"bar_minutes": 1}, "buy": {"delay_bar": 0,
             "groups": [{"enabled": True, "conditions": [{"enabled": True,
             "target": "CLOSE", "operator": ">=", "value": 150}]}]},
             "sell": {"filters": {}, "signals": {}}}
    def supply():
        return mock._instance_candle_projection(
            {"session": {"stock_code": CODE}}, instance_id="RI-1", rules=rules,
            settings={}, now=clock[0], projection_request=REQUEST)
    first = supply()
    assert first["available"] is True, first
    _tick(host, broker, clock)
    current = supply()
    assert current["available"] is True, current
    assert current["candles"][-1]["close"] == 150
    selected = adapter._pure_signal_selection()(
        current["candles"], rules,
        {"candles": current["candles"], "cycle": {"status": "resolved",
         "holding_qty": 0}, "routine_instance_id": "RI-1", "code": CODE,
         "probe_only": True, "_indicator_follow_validation_current_price": 150},
        evaluator=mock.routine_adapter._evaluator,
        converter=adapter._signal_payload)
    assert selected["signal"] == "BUY", selected
    broker._v5_al_receive_sequence_by_code[CODE] = 2
    assert supply()["available"] is False
    host.sync_candle_observation_targets([])
    assert supply()["available"] is False


def test_mock_current_joins_additional_confirmed_native_tail(tmp_path, monkeypatch):
    import mock_validation_candle_provider as provider
    import mock_validation_host as mock_module
    from mock_validation_host import MockValidationHost
    from routines.지표추종매매 import routine

    host, broker, clock = _host(monkeypatch)
    clock[0] = clock[0].replace(minute=7)
    monkeypatch.setattr(provider, "read_stock_market_source_plan", lambda *_: PLAN)
    monkeypatch.setattr(mock_module, "read_stock_market_source_plan", lambda *_: PLAN)
    host.sync_candle_observation_targets([_requirement()])
    history = [_opt10080_row(
        (datetime(2026, 10, day, 9, tzinfo=SEOUL_TIMEZONE)
         + timedelta(minutes=3 * index)).strftime("%Y%m%d%H%M%S"), 100)
        for day, count in ((21, 100), (22, 100), (23, 99))
        for index in range(count)]
    history.append(_opt10080_row("20261026090000", 100))
    assert host.install_display_timeframe_candles(
        CODE, 3, history, as_of=clock[0], market_data_identity=f"{CODE}_AL",
        market_source="INTEGRATED", display_only=True,
        connection_epoch=SESSION[0], login_session_id=SESSION[1])["ok"] is True
    broker.response_rows = [_opt10080_row("20261026090300", 151),
                            _opt10080_row("20261026090600", 100)]
    host._refresh_v5_observation(now=clock[0])
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]
    with patch.object(host_module, "datetime", Clock):
        broker.finish()
    def read_value(**kwargs):
        value = deepcopy(host.project_routine_candles(**kwargs))
        value["calculation_as_of"] = kwargs["as_of"].isoformat()
        return value
    mock = MockValidationHost(
        broker, project_root=tmp_path, candles_provider=lambda **_: None,
        operation_policy_provider=lambda: {},
        sor_calculation_value_reader=read_value,
        sor_native_current_reader=lambda code, frame:
            host.native_selected_current_snapshot(code, frame, consumer="MOCK"))
    rules = {"bar": {"bar_minutes": 3}, "buy": {"delay_bar": 0,
             "groups": [{"enabled": True, "conditions": [{"enabled": True,
             "target": "CLOSE", "operator": ">=", "value": 150}]}]},
             "sell": {"filters": {}, "signals": {}}}
    def supply():
        return mock._instance_candle_projection(
            {"session": {"stock_code": CODE}}, instance_id="RI-1", rules=rules,
            settings={}, now=clock[0], projection_request=REQUEST)
    current = supply()
    assert current["available"] is True, current
    assert [row["bar_time"][11:16] for row in current["candles"][-3:]] == [
        "09:00", "09:03", "09:06"]
    assert current["candles"][-2]["close"] == 152
    assert current["direct_source_receipt"]["source_content_sha256"] != (
        current["source_identity"])
    _tick(host, broker, clock)
    current = supply()
    assert current["candles"][-2]["close"] == 152
    observed = []
    def selected(candles, config, context):
        observed.append(deepcopy(candles[-3:]))
        return routine.evaluate_current_signal_selection(candles, config, context)
    assert selected(current["candles"], rules, {
        "candles": current["candles"], "cycle": {"status": "resolved", "holding_qty": 0},
        "routine_instance_id": "RI-1", "code": CODE, "probe_only": True,
        "_indicator_follow_validation_current_price": 150})["signal"] == "BUY"
    assert observed[-1][-2]["close"] == 152


def test_native_current_tail_keeps_m1_1535_display_only_and_repairs_1540():
    from hashlib import sha256
    from candle_manager import canonical_candle_content_hash
    from kiwoom_candle_adapter import normalize_opt10080_rows
    from sor_native_selected_current_join import join_native_selected_completed
    from stock_market_source_plan import split_native_closing_price_candles

    as_of = datetime(2026, 10, 26, 15, 41, 30, tzinfo=SEOUL_TIMEZONE)
    base_raw = [_opt10080_row("20261026151900", 100),
                _opt10080_row("20261026154000", 100)]
    base = normalize_opt10080_rows(base_raw)
    source = {"candles": [{**row,
              "bar_time": datetime.strptime(row["time"], "%Y%m%d%H%M%S")
              .replace(tzinfo=SEOUL_TIMEZONE).isoformat(),
              "is_complete": True} for row in base],
              "direct_source_receipt": {
                  "stock_code": CODE, "timeframe_key": "M1",
                  "connection_epoch": SESSION[0],
                  "session_sha256": sha256(SESSION[1].encode()).hexdigest(),
                  "source_as_of": as_of.isoformat(),
                  "source_content_sha256": canonical_candle_content_hash(base)}}
    native_raw = [_opt10080_row("20261026153500", 100),
                  _opt10080_row("20261026154000", 150),
                  _opt10080_row("20261026154100", 100)]
    native = normalize_opt10080_rows(native_raw)
    completed = [{**row, "is_complete": True,
                  "market_source": "INTEGRATED",
                  "market_data_identity": f"{CODE}_AL",
                  "native_bar_end": datetime.strptime(row["time"], "%Y%m%d%H%M%S")
                  .replace(tzinfo=SEOUL_TIMEZONE).replace(
                      minute=int(row["time"][10:12]) + 1).isoformat()}
                 for row in native[:2]]
    calculation, display = split_native_closing_price_candles(
        completed, timeframe_minutes=1,
        market_data_identity=f"{CODE}_AL", market_source="INTEGRATED")
    assert [row["time"] for row in calculation] == ["20261026154000"]
    assert display[0]["display_only_special"] == "BROKER_AL_NATIVE_1535_CLOSING_PRICE"
    started = {"rqname": "native-special",
               "request_as_of": (as_of - timedelta(seconds=4)).isoformat()}
    response = {"rqname": "native-special", "interval": 1,
                "market_data_identity": f"{CODE}_AL",
                "connection_epoch": SESSION[0], "login_session_id": SESSION[1],
                "rows": native_raw}
    proof = {"stock_code": CODE, "timeframe_minutes": 1,
             "market_source": "INTEGRATED", "market_data_identity": f"{CODE}_AL",
             "session_identity": SESSION, "request_id": "native-special",
             "request_receipt": (started, response), "completed_candles": display,
             "forming_candle": {**native[2], "is_complete": False}}
    original_source, original_proof = deepcopy(source), deepcopy(proof)
    joined, tail_identity = join_native_selected_completed(
        source, proof, stock_code=CODE, interval=1, as_of=as_of)
    assert [row["time"] for row in joined] == ["20261026151900", "20261026154000"]
    assert joined[-1]["close"] == 151
    assert tail_identity and tail_identity != source["direct_source_receipt"]["source_content_sha256"]
    assert source == original_source and proof == original_proof
    unmarked = deepcopy(proof)
    unmarked["completed_candles"][0].pop("display_only_special")
    calculation_rows, _ = join_native_selected_completed(
        source, unmarked, stock_code=CODE, interval=1, as_of=as_of)
    assert all(row["time"] != "20261026153500" for row in calculation_rows)
    upper_source, upper_proof = deepcopy(source), deepcopy(unmarked)
    upper_source["direct_source_receipt"]["timeframe_key"] = "M3"
    upper_source["direct_source_receipt"]["source_content_sha256"] = (
        canonical_candle_content_hash(upper_source["candles"]))
    upper_proof["timeframe_minutes"] = upper_proof["request_receipt"][1]["interval"] = 3
    with pytest.raises(ValueError, match="SPECIAL_BAR_AMBIGUOUS"):
        join_native_selected_completed(
            upper_source, upper_proof, stock_code=CODE, interval=3, as_of=as_of)


@pytest.mark.parametrize("frame", (1, 3))
def test_no_v5_production_current_uses_selected_m1_or_corrected_direct_tail(
        frame, tmp_path, monkeypatch):
    import routine_instance_registry as registry
    import routine_package_contract as contract
    import stock_repository
    import sor_no_order_monitor as monitor_module
    from routine_main_facts import build_routine_main_facts_from_projection
    from routines.지표추종매매 import routine

    app = QCoreApplication.instance() or QCoreApplication([])
    host, broker, clock = _host(monkeypatch)
    host.sync_candle_standby_requirements([_requirement(frame)])
    host.sync_monitoring_targets((CODE,))
    assert host._v5_observation_leases == {}
    if frame == 1:
        history = [_opt10080_row(
            (datetime(2026, 10, day, 9, tzinfo=SEOUL_TIMEZONE)
             + timedelta(minutes=index)).strftime("%Y%m%d%H%M%S"), 100)
            for day, count in ((21, 100), (22, 100), (23, 96))
            for index in range(count)]
        history += [_opt10080_row(f"2026102609{minute:02d}00", 100)
                    for minute in range(4)]
        broker.response_rows = [_opt10080_row("20261026090300", 100),
                                _opt10080_row("20261026090400", 100)]
    else:
        history = [_opt10080_row(
            (datetime(2026, 10, day, 9, tzinfo=SEOUL_TIMEZONE)
             + timedelta(minutes=3 * index)).strftime("%Y%m%d%H%M%S"), 100)
            for day, count in ((21, 100), (22, 100), (23, 99))
            for index in range(count)]
        history.append(_opt10080_row("20261026090000", 100))
        broker.response_rows = [_opt10080_row("20261026090000", 150),
                                _opt10080_row("20261026090300", 100)]
    assert host.install_display_timeframe_candles(
        CODE, frame, history, as_of=clock[0], market_data_identity=f"{CODE}_AL",
        market_source="INTEGRATED", display_only=True,
        connection_epoch=SESSION[0], login_session_id=SESSION[1])["ok"] is True
    host._refresh_v5_observation(now=clock[0])
    assert broker.requests == [(CODE, frame, 3)]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]
    with patch.object(host_module, "datetime", Clock):
        broker.finish()

    stock_dir = tmp_path / "stock"
    stock_dir.mkdir()
    (stock_dir / "config.json").write_text(json.dumps({
        "assigned_routine_instance_id": "RI-1", "calculation_market_source": "SOR"}))
    rules = {"enabled": True, "bar": {"bar_minutes": frame},
             "buy": {"delay_bar": 0, "groups": [{"enabled": True,
             "conditions": [{"enabled": True, "target": "CLOSE",
             "operator": ">=", "value": 150}]}]},
             "sell": {"filters": {}, "signals": {}}}
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps(rules))
    package = tmp_path / "routines" / "지표추종매매"
    package.mkdir(parents=True)
    monkeypatch.setattr(monitor_module, "_ROOT", tmp_path)
    monkeypatch.setattr(monitor_module, "datetime", Clock)
    monkeypatch.setattr(monitor_module, "read_stock_market_source_plan", lambda *_: PLAN)
    monkeypatch.setattr(stock_repository, "StockRepository", lambda **_:
                        SimpleNamespace(resolve_stock_dir=lambda _code: stock_dir))
    monkeypatch.setattr(registry, "routine_instance_by_id", lambda *_a, **_kw:
                        SimpleNamespace(enabled=True, persisted=True,
                                        rules_path=rules_path, definition_id="D-1"))
    monkeypatch.setattr(registry, "routine_definition_by_id", lambda *_a, **_kw:
                        SimpleNamespace(package_enabled=True, package_dir=package))
    observed = []
    def selected(candles, config, context):
        observed.append(deepcopy(candles[-3:]))
        return routine.evaluate_current_signal_selection(candles, config, context)
    monkeypatch.setattr(contract, "load_routine_module", lambda *_:
                        SimpleNamespace(
                            evaluate_current_signal_selection=selected,
                            market_bar_projection_request=routine.market_bar_projection_request,
                            project_cycle_context=routine.project_cycle_context))
    facts = build_routine_main_facts_from_projection({
        "stock_configs": {CODE: {"assigned_routine_instance_id": "RI-1"}},
        "orders": {"orders": []}, "fills": {"fills": []},
        "positions": {"positions": []}, "signals": {"signals": []}},
        now=clock[0] - timedelta(seconds=1)).to_payload()
    assert routine.project_cycle_context(
        code=CODE, routine_instance_id="RI-1", order_queue=facts["orders"],
        fills=facts["fills"], positions=facts["positions"],
        signals=facts["signals"])["status"] == "resolved"
    assert monitor_module._prepare({
        "source": "NATIVE_SELECTED_TICK_ACCEPTED", "stock_code": CODE,
        "timeframe_minutes": frame, "observed_at": clock[0].isoformat()}, facts)
    monitor = monitor_module.NoOrderSorMonitor(host, host)
    monitor.accept_main_facts(facts)
    try:
        _tick(host, broker, clock)
        deadline = time.monotonic() + 3
        while (monitor.last_current_result.get("status") not in {"OBSERVED", "BLOCKED"}
               or monitor.last_current_result.get("reason") == "ACTUAL_CYCLE_MISSING") and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.002)
        app.processEvents()
        assert monitor.last_current_result["status"] == "OBSERVED", monitor.last_current_result
        assert monitor.last_current_result["signal"] == "SKIP"
        assert monitor.last_current_result["sor_preview_signal"] == "BUY"
        assert observed and observed[-1][-1]["close"] == 150
        if frame == 3:
            assert observed[-1][-2]["close"] == 151
        assert monitor.last_current_result["execution_enabled"] is False
        assert monitor.last_current_result["queue_connected"] is False
        assert monitor.last_current_result["send_order_connected"] is False
        broker._v5_al_receive_sequence_by_code[CODE] = 2
        assert host.native_selected_current_snapshot(CODE, frame, consumer="PRODUCTION") is None
    finally:
        monitor.shutdown()
