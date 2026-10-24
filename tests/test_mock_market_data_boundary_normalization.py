# -*- coding: utf-8 -*-

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from mock_validation_candle_provider import project_mock_routine_candles, project_mock_sor_calculation_snapshot


def test_direct_m3_producer_reaches_mock_host_and_source_marker():
    from copy import deepcopy
    from candle_manager import canonical_candle_content_hash
    from gui_market_data_host import MarketDataHost
    from sor_routine_signal_evidence import build_source_marker
    from tests.test_sor_core_m1_input import NOW, _installed_host

    market = _installed_host()
    first = dict(market._display_timeframe_candles[("005930", 1)][0])
    second = dict(first, bar_time="2026-10-20T09:03:00+09:00", close=101)
    first.update(timeframe_minutes=3)
    second.update(timeframe_minutes=3)
    market._display_timeframe_candles[("005930", 3)] = (first, second)
    market._display_timeframe_session_identity[("005930", 3)] = (1, "SESSION-1")
    market._display_timeframe_candles.pop(("005930", 1))
    now = NOW + timedelta(minutes=1)
    rules = {"bar": {"bar_minutes": 3}}
    request = {"projection": "COMPLETED_TIMEFRAME", "warmup_bars": 1,
               "history_target_bars": 1}

    def reader(**kwargs):
        return MarketDataHost.project_routine_candles(market, **kwargs)

    with tempfile.TemporaryDirectory() as temporary:
        from mock_validation_host import MockValidationHost
        host = MockValidationHost(_Api(), project_root=Path(temporary),
                                  sor_calculation_value_reader=reader,
                                  now_factory=lambda: now)
        try:
            document = {"session": {"stock_code": "005930"},
                        "reference_snapshot": {"calculation_market_source": "SOR"}}
            value = reader(stock_code="005930", rules=rules,
                           projection_request=request, as_of=now,
                           consumer_scope="SOR_CALCULATION_ONLY")
            assert value["available"] is True
            assert value["candles"] == [second]
            assert value["source_identity"] == canonical_candle_content_hash([second])
            admitted = host._instance_candle_projection(
                document, instance_id="A", rules=rules, settings={}, now=now,
                projection_request=request)
            assert admitted["available"] is True
            assert admitted["candles"] == [second]
            assert admitted["candles"][0] is not value["candles"][0]
            assert admitted["direct_source_receipt"] is not value["direct_source_receipt"]
            assert build_source_marker(stock="005930", rules=rules,
                                       candles=admitted["candles"], source=admitted,
                                       side="BUY") is not None
            for field, replacement in (
                ("direct_source_receipt", None),
                ("source_identity", "f" * 64),
                ("direct_history_source", ""),
                ("complete", False),
            ):
                bad = deepcopy(value)
                bad[field] = replacement
                assert project_mock_sor_calculation_snapshot(
                    bad, stock_code="005930", rules=rules,
                    projection_request=request, as_of=now)["available"] is False
            for receipt_change in (
                {"stock_code": "000660"}, {"timeframe_key": "M5"},
                {"connection_epoch": 0}, {"session_sha256": ""},
                {"source_as_of": NOW.isoformat()},
            ):
                bad = deepcopy(value)
                bad["direct_source_receipt"].update(receipt_change)
                assert project_mock_sor_calculation_snapshot(
                    bad, stock_code="005930", rules=rules,
                    projection_request=request, as_of=now)["available"] is False
            for row_change in (
                {"market_data_identity": "005930"}, {"market_source": "KRX"},
                {"display_only": False}, {"close": 102},
            ):
                bad = deepcopy(value)
                bad["candles"][0].update(row_change)
                assert project_mock_sor_calculation_snapshot(
                    bad, stock_code="005930", rules=rules,
                    projection_request=request, as_of=now)["available"] is False
            for rows in ([], [value["candles"][0]] * 2):
                bad = deepcopy(value)
                bad["candles"] = rows
                assert project_mock_sor_calculation_snapshot(
                    bad, stock_code="005930", rules=rules,
                    projection_request=request, as_of=now)["available"] is False
            bad = deepcopy(value)
            bad["calculation_source"] = "BROKER_AL_CONFIRMED_M1"
            assert project_mock_sor_calculation_snapshot(
                bad, stock_code="005930", rules=rules,
                projection_request=request, as_of=now)["available"] is False
            for required in ({"warmup_bars": 2}, {"warmup_bars": 1,
                                                  "history_target_bars": 2}):
                assert project_mock_sor_calculation_snapshot(
                    value, stock_code="005930", rules=rules,
                    projection_request={"projection": "COMPLETED_TIMEFRAME", **required},
                    as_of=now)["available"] is False
        finally:
            host.dispose()


def test_higher_forming_uses_only_direct_completed_prefix():
    from copy import deepcopy
    from tests.test_sor_core_m1_input import NOW, _forming_host, _project

    market = _forming_host()
    direct = dict(market._display_timeframe_candles[("005930", 1)][0],
                  timeframe_minutes=3)
    market._display_timeframe_candles[("005930", 3)] = (direct,)
    market._display_timeframe_session_identity[("005930", 3)] = (1, "SESSION-1")
    source = _project(market, 3, projection="FORMING_BASE_BAR")
    assert source["available"] is True
    assert source["candles"][0] == direct
    assert source["candles"][-1]["is_complete"] is False
    request = {"projection": "FORMING_BASE_BAR", "warmup_bars": 1}
    admitted = project_mock_sor_calculation_snapshot(
        source, stock_code="005930", rules={"bar": {"bar_minutes": 3}},
        projection_request=request, as_of=NOW)
    assert admitted["available"] is True
    assert admitted["candles"][0] == direct
    bad = deepcopy(source)
    bad["candles"][0]["close"] += 1
    assert project_mock_sor_calculation_snapshot(
        bad, stock_code="005930", rules={"bar": {"bar_minutes": 3}},
        projection_request=request, as_of=NOW)["available"] is False


def test_mock_sor_receipt_is_detached_and_tampering_blocks():
    from copy import deepcopy
    from tests.test_sor_core_m1_input import NOW, _forming_host, _project

    source = _project(_forming_host(), 1, projection="FORMING_BASE_BAR")
    source["calculation_as_of"] = NOW.isoformat()
    args = dict(stock_code="005930", rules={"bar": {"bar_minutes": 1}},
                projection_request={"projection": "FORMING_BASE_BAR"}, as_of=NOW)
    copied = project_mock_sor_calculation_snapshot(source, **args)
    assert copied["available"] is True
    assert copied["sor_source_receipt"] == source["sor_source_receipt"]
    assert copied["sor_source_receipt"] is not source["sor_source_receipt"]
    assert all(copied[key] is False for key in
               ("execution_enabled", "queue_connected", "send_order_connected"))
    missing = deepcopy(source)
    missing.pop("sor_source_receipt")
    without_proof = project_mock_sor_calculation_snapshot(missing, **args)
    assert without_proof["available"] is True
    assert "sor_source_receipt" not in without_proof
    for field, value in (
        ("stock_code", "000660"), ("timeframe_key", "M5"),
        ("source_as_of", (NOW - timedelta(minutes=1)).isoformat()),
        ("received_at", (NOW - timedelta(minutes=1)).isoformat()),
        ("source_proof", "INDEPENDENT_MERGE"),
        ("independent_merge_verified", True),
    ):
        bad = deepcopy(source)
        bad["sor_source_receipt"][field] = value
        assert project_mock_sor_calculation_snapshot(bad, **args)["available"] is False
    bad = deepcopy(source)
    bad["sor_source_receipt"] = None
    assert project_mock_sor_calculation_snapshot(bad, **args)["available"] is False
    bad = deepcopy(source)
    bad["sor_source_receipt"]["login_session_id"] = "SESSION-1"
    assert project_mock_sor_calculation_snapshot(bad, **args)["available"] is False


SEOUL = timezone(timedelta(hours=9))


class _Signal:
    def connect(self, _callback) -> None:
        pass

    def disconnect(self, _callback) -> None:
        pass


class _Api:
    def __init__(self) -> None:
        self.mock_orderbook_received = _Signal()
        self.realtime_shadow_tick_received = _Signal()
        self.login_state_changed = _Signal()

    def sync_mock_orderbook_registration(self, targets, *, sor_stock_codes=()):
        return {
            "ok": True,
            "snapshot": {
                "active": False,
                "connection_epoch": 0,
                "login_session_id": "",
                "target_stock_codes": tuple(targets),
            },
        }

    def clear_mock_orderbook_registration(self, *, reason: str):
        return {"ok": True, "reason": reason}

    def current_realtime_shadow_bar(self, _stock_code):
        return None


def _production_projection(self, **_kwargs):
    self.projection_calls += 1
    return {"available": True, "candles": []}


def _production_updater(self, _targets):
    self.update_calls += 1
    self.targets = ("MUTATED",)


ProductionMarketDataHost = type(
    "MarketDataHost",
    (),
    {
        "__module__": "gui_market_data_host",
        "project_routine_candles": _production_projection,
        "sync_candle_observation_targets": _production_updater,
    },
)


class MockMarketDataBoundaryNormalizationTest(unittest.TestCase):
    def test_sor_reference_is_default_and_explicit_krx_is_frozen(self) -> None:
        from mock_validation_reference_snapshot import build_mock_reference_snapshot

        args = {"routine_instances": [{"instance_id": "R", "definition_id": "D",
                                      "routine_type": "INDICATOR_FOLLOW"}],
                "rules_by_instance_id": {"R": {}}, "created_at": "2026-09-12T09:00:00+09:00"}
        sor = build_mock_reference_snapshot(stock={"code": "005930", "name": "Samsung"}, **args)
        krx = build_mock_reference_snapshot(stock={"code": "005930", "name": "Samsung",
                                                   "calculation_market_source": "KRX"}, **args)
        self.assertEqual("SOR", sor["calculation_market_source"])
        self.assertEqual("KRX", krx["calculation_market_source"])
        self.assertNotEqual(krx["snapshot_hash"], sor["snapshot_hash"])

    def test_sor_runtime_cycle_cannot_enter_virtual_execution(self) -> None:
        from mock_validation_host import MockValidationHost

        with tempfile.TemporaryDirectory() as temporary:
            host = MockValidationHost(_Api(), project_root=Path(temporary))
            try:
                document = {"session": {"stock_code": "005930", "state": "RUNNING"},
                            "reference_snapshot": {}}
                with patch.object(host.routine_adapter, "evaluate_cycle", side_effect=AssertionError), \
                     patch.object(host, "_drain_market_evidence", side_effect=AssertionError):
                    self.assertIs(host._process_stock(document, datetime(2026, 9, 12, 9, 7, tzinfo=SEOUL)), document)
            finally:
                host.dispose()

    def test_sor_value_reader_accepts_only_detached_proven_rows(self) -> None:
        from copy import deepcopy
        from mock_validation_host import MockValidationHost

        now = datetime(2026, 9, 12, 9, 7, tzinfo=SEOUL)
        row = {"bar_time": "2026-09-12T09:06:00+09:00", "open": 100,
               "high": 101, "low": 99, "close": 100, "volume": 10,
               "timeframe_minutes": 1, "is_complete": True,
               "trade_date": "2026-09-12", "session": "regular"}
        supplied = {"available": True, "stock_code": "005930", "timeframe_minutes": 1,
                    "calculation_as_of": now.isoformat(),
                    "projection": "COMPLETED_TIMEFRAME", "market_source": "INTEGRATED",
                    "market_data_identity": "005930_AL",
                    "calculation_source": "BROKER_AL_CONFIRMED_M1",
                    "source_proof": "BROKER_ROWS_NOT_INDEPENDENT_MERGE_ATTESTATION",
                    "calculation_only": True, "execution_enabled": False,
                    "queue_connected": False, "send_order_connected": False,
                    "candles": [row]}
        original = deepcopy(supplied)
        calls = []
        with tempfile.TemporaryDirectory() as temporary:
            host = MockValidationHost(_Api(), project_root=Path(temporary),
                                      sor_calculation_value_reader=lambda **kw: (calls.append(kw), supplied)[1],
                                      now_factory=lambda: now)
            try:
                document = {"session": {"stock_code": "005930"},
                            "reference_snapshot": {"calculation_market_source": "SOR"}}
                result = host._instance_candle_projection(
                    document, instance_id="R", rules={"bar": {"bar_minutes": 1}},
                    settings={}, now=now,
                    projection_request={"projection": "COMPLETED_TIMEFRAME", "warmup_bars": 1})
                self.assertTrue(result["available"])
                self.assertIsNot(result["candles"][0], supplied["candles"][0])
                self.assertEqual(original, supplied)
                self.assertEqual("SOR_CALCULATION_ONLY", calls[0]["consumer_scope"])
                with patch.object(host, "_candles_provider", side_effect=AssertionError):
                    self.assertTrue(host._instance_candle_projection(
                        document, instance_id="R", rules={"bar": {"bar_minutes": 1}},
                        settings={}, now=now,
                        projection_request={"projection": "COMPLETED_TIMEFRAME", "warmup_bars": 1})["available"])
                for change in ({"market_source": "KRX"}, {"source_proof": ""},
                               {"stock_code": "000660"}, {"queue_connected": True},
                               {"calculation_as_of": (now - timedelta(minutes=1)).isoformat()}):
                    bad = deepcopy(supplied)
                    bad.update(change)
                    host._sor_calculation_value_reader = lambda **_kw: bad
                    self.assertFalse(host._instance_candle_projection(
                        document, instance_id="R", rules={"bar": {"bar_minutes": 1}},
                        settings={}, now=now,
                        projection_request={"projection": "COMPLETED_TIMEFRAME", "warmup_bars": 1})["available"])
                stale = deepcopy(supplied)
                stale["candles"][0]["bar_time"] = "2026-09-11T09:05:00+09:00"
                self.assertFalse(project_mock_sor_calculation_snapshot(
                    stale, stock_code="005930", rules={"bar": {"bar_minutes": 1}},
                    projection_request={"projection": "COMPLETED_TIMEFRAME", "warmup_bars": 1},
                    as_of=now)["available"])
                host._sor_calculation_value_reader = None
                with patch.object(host, "_candles_provider", side_effect=AssertionError):
                    self.assertFalse(host._instance_candle_projection(
                        document, instance_id="R", rules={"bar": {"bar_minutes": 1}},
                        settings={}, now=now,
                        projection_request={"projection": "COMPLETED_TIMEFRAME", "warmup_bars": 1})["available"])
            finally:
                host.dispose()

    def test_production_market_data_callbacks_are_not_used_by_mock(self) -> None:
        from mock_validation_host import MockValidationHost

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            production = ProductionMarketDataHost()
            production.projection_calls = 0
            production.update_calls = 0
            production.targets = ("PRODUCTION",)
            host = MockValidationHost(
                _Api(),
                project_root=root,
                candles_provider=production.project_routine_candles,
                candle_observation_updater=production.sync_candle_observation_targets,
                sor_calculation_value_reader=production.project_routine_candles,
                now_factory=lambda: datetime(2026, 9, 12, 9, 10, tzinfo=SEOUL),
            )
            try:
                host.sync_registration()
                self.assertIsNone(host._candle_observation_updater)
                self.assertIsNone(host._sor_calculation_value_reader)
                self.assertIsNot(
                    getattr(host._candles_provider, "__self__", None),
                    production,
                )
            finally:
                host.dispose()
            self.assertEqual(("PRODUCTION",), production.targets)
            self.assertEqual(0, production.update_calls)
            self.assertEqual(0, production.projection_calls)

    def test_historical_diagnostic_uses_direct_read_only_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            class ReadOnlyApi(_Api):
                def request_minute_candles_read_only(self, code, *, interval, count, callback):
                    self.request = (code, interval, count)
                    callback({"ok": True, "code": code, "interval": interval, "market_source": "KRX", "rows": [{}]})
                    return {"ok": True}

            api = ReadOnlyApi()
            with patch(
                "mock_validation_candle_provider.normalize_opt10080_rows",
                return_value=[{"time": "20260912090000", "open": 100, "high": 101,
                               "low": 99, "close": 100, "volume": 10}],
            ):
                result = project_mock_routine_candles(
                    project_root=root, api=api, stock_code="005930",
                    consumer_scope="HISTORICAL_DIAGNOSTIC",
                    rules={"bar": {"bar_minutes": 5}},
                    projection_request={"projection": "COMPLETED_TIMEFRAME", "warmup_bars": 1},
                    as_of=datetime(2026, 9, 12, 9, 7, tzinfo=SEOUL),
                    session_windows=({"name": "regular", "start_time": "09:00:00", "end_time": "15:20:00"},),
                    identity_reader=lambda _code: ("MV-current", 1, "SESSION-1", "1:1"),
                )
            self.assertEqual(("005930", 5, 3), api.request)
            self.assertTrue(result["available"])
            self.assertEqual(1, len(result["candles"]))
            self.assertEqual(100, result["candles"][0]["close"])
            self.assertEqual(5, result["candles"][0]["timeframe_minutes"])

    def test_direct_candle_callback_rejects_replaced_mock_session(self) -> None:
        class DelayedApi(_Api):
            def request_minute_candles_read_only(self, code, *, interval, count, callback):
                self.callback = callback
                return {"ok": True}

        api = DelayedApi()
        identity = ["MV-old", 1, "SESSION-1", "1:1"]
        cache, pending = {}, set()
        kwargs = dict(
            project_root=".", api=api, stock_code="122350",
            consumer_scope="HISTORICAL_DIAGNOSTIC",
            rules={"bar": {"bar_minutes": 5}},
            projection_request={"projection": "COMPLETED_TIMEFRAME", "warmup_bars": 1},
            as_of=datetime(2026, 9, 12, 9, 7, tzinfo=SEOUL),
            cache=cache, pending=pending,
            identity_reader=lambda _code: tuple(identity),
        )
        self.assertFalse(project_mock_routine_candles(**kwargs)["available"])
        identity[0] = "MV-new"
        api.callback({"ok": True, "code": "122350", "interval": 5,
                      "market_source": "KRX", "rows": [{}]})
        self.assertFalse(project_mock_routine_candles(**kwargs)["available"])
        self.assertNotIn("rows", cache[("122350", 5)])

    def test_direct_forming_bar_uses_source_row_and_mock_trade(self) -> None:
        class ReadOnlyApi(_Api):
            def request_minute_candles_read_only(self, code, *, interval, count, callback):
                callback({"ok": True, "code": code, "interval": interval,
                          "market_source": "KRX", "rows": [{}, {}]})
                return {"ok": True}

        rows = [
            {"time": "20260912090000", "open": 100, "high": 101,
             "low": 99, "close": 100, "volume": 10},
            {"time": "20260912090500", "open": 102, "high": 103,
             "low": 101, "close": 102, "volume": 5},
        ]
        with patch("mock_validation_candle_provider.normalize_opt10080_rows", return_value=rows):
            result = project_mock_routine_candles(
                project_root=".", api=ReadOnlyApi(), stock_code="122350",
                consumer_scope="HISTORICAL_DIAGNOSTIC",
                rules={"bar": {"bar_minutes": 5}},
                projection_request={"projection": "FORMING_BASE_BAR", "warmup_bars": 1},
                as_of=datetime(2026, 9, 12, 9, 7, tzinfo=SEOUL),
                identity_reader=lambda _code: ("MV-current", 1, "SESSION-1", "1:1"),
                latest_trade=SimpleNamespace(
                    market_datetime="2026-09-12T09:07:00+09:00", current_price=105
                ),
            )
        self.assertTrue(result["available"])
        self.assertEqual([True, False], [bar["is_complete"] for bar in result["candles"]])
        self.assertEqual(105, result["candles"][-1]["close"])
        self.assertEqual(5, result["candles"][-1]["timeframe_minutes"])


if __name__ == "__main__":
    unittest.main()
