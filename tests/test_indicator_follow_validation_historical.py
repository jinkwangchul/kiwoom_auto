# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path
import unittest
from unittest.mock import Mock

from routines.지표추종매매.routine_validation_contract import (
    ValidationRequest,
    ValidationSettingsSnapshot,
    ValidationStockRef,
)
from routines.지표추종매매.routine_validation_historical import (
    REASON_BROKER_REQUEST_ERROR,
    REASON_BROKER_REQUESTER_UNAVAILABLE,
    REASON_HISTORICAL_REQUEST_FAILED,
    REASON_INVALID_COUNT,
    REASON_INVALID_HISTORICAL_RESPONSE,
    ValidationHistoricalMemoryCache,
    ValidationHistoricalProvider,
    ValidationHistoricalSnapshot,
)
from routines.지표추종매매.routine_validation_session import ValidationSession


class _FakeBroker:
    def __init__(self, *, invocation_error: Exception | None = None) -> None:
        self.invocation_error = invocation_error
        self.calls: list[dict[str, object]] = []
        self.callback = None
        self.request_minute_candles = Mock()

    def request_minute_candles_read_only(
        self,
        code: str,
        name: str,
        *,
        interval: int,
        count: int,
        callback,
    ) -> dict[str, object]:
        if self.invocation_error is not None:
            raise self.invocation_error
        self.calls.append(
            {
                "code": code,
                "name": name,
                "interval": interval,
                "count": count,
                "callback": callback,
            }
        )
        self.callback = callback
        return {"ok": True, "status": "REQUESTED"}

    def request_period_candles_read_only(
        self,
        code: str,
        name: str,
        *,
        timeframe_key: str,
        count: int,
        callback,
    ) -> dict[str, object]:
        if self.invocation_error is not None:
            raise self.invocation_error
        self.calls.append(
            {
                "code": code,
                "name": name,
                "timeframe_key": timeframe_key,
                "count": count,
                "callback": callback,
            }
        )
        self.callback = callback
        return {"ok": True, "status": "REQUESTED"}

    def complete(self, response: object) -> None:
        if not callable(self.callback):
            raise AssertionError("request callback is unavailable")
        self.callback(response)


class _DedicatedPeriodBroker:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.callback = None

    def _request(self, method: str, code: str, name: str, *, count: int, callback):
        self.calls.append(
            {
                "method": method,
                "code": code,
                "name": name,
                "count": count,
                "callback": callback,
            }
        )
        self.callback = callback
        return {"ok": True, "status": "REQUESTED"}

    def request_day_candles_read_only(self, code, name, *, count, callback):
        return self._request("OPT10081", code, name, count=count, callback=callback)

    def request_week_candles_read_only(self, code, name, *, count, callback):
        return self._request("OPT10082", code, name, count=count, callback=callback)

    def request_year_candles_read_only(self, code, name, *, count, callback):
        return self._request("OPT10094", code, name, count=count, callback=callback)

    def complete(self, response: object) -> None:
        self.callback(response)


class ValidationHistoricalProviderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.stock = ValidationStockRef("005930", "삼성전자")
        self.settings = ValidationSettingsSnapshot(
            {"bar": {"bar_minutes": 3}}
        )
        self.request = ValidationRequest(self.stock, self.settings, 3)
        self.rows = [
            {
                "체결시간": "20260913090100",
                "시가": "70000",
                "고가": "70100",
                "저가": "69900",
                "현재가": "70050",
                "거래량": "100",
            },
            {
                "체결시간": "20260913090000",
                "시가": "69900",
                "고가": "70000",
                "저가": "69800",
                "현재가": "70000",
                "거래량": "90",
            },
        ]

    def _provider(self, reader, broker=None, cache=None):
        session = ValidationSession(
            self.request,
            operation_active_reader=reader,
        )
        selected_broker = broker if broker is not None else _FakeBroker()
        provider = ValidationHistoricalProvider(
            session,
            selected_broker,
            cache=cache,
        )
        return provider, selected_broker

    def _valid_response(self, **changes) -> dict[str, object]:
        response: dict[str, object] = {
            "ok": True,
            "type": "minute_candles",
            "request_id": "opt10080_005930_1",
            "code": "005930",
            "name": "삼성전자",
            "interval": 3,
            "rows": self.rows,
            "rows_count": len(self.rows),
        }
        response.update(changes)
        return response

    def test_invalid_count_never_calls_broker_or_cache(self) -> None:
        for count in (True, False, 0, -1, 1.0, "1", None):
            with self.subTest(count=count):
                provider, broker = self._provider(Mock(return_value=False))
                results = []

                returned = provider.request_latest(count, results.append)

                self.assertEqual(REASON_INVALID_COUNT, returned.reason)
                self.assertEqual([returned], results)
                self.assertEqual([], broker.calls)
                self.assertEqual(0, provider.cache.size)

    def test_operation_state_does_not_block_historical_request(self) -> None:
        for reader in (
            Mock(return_value=True),
            Mock(side_effect=RuntimeError("must not be called")),
        ):
            with self.subTest(reader=reader):
                provider, broker = self._provider(reader)
                results = []

                returned = provider.request_latest(2, results.append)

                self.assertIsNone(returned)
                self.assertEqual(1, len(broker.calls))
                self.assertEqual(0, provider.cache.size)
                reader.assert_not_called()

    def test_valid_request_uses_only_read_only_broker_method(self) -> None:
        reader = Mock(side_effect=(False, False))
        provider, broker = self._provider(reader)
        results = []

        returned = provider.request_latest(2, results.append)

        self.assertIsNone(returned)
        self.assertEqual(1, len(broker.calls))
        call = broker.calls[0]
        self.assertEqual("005930", call["code"])
        self.assertEqual("삼성전자", call["name"])
        self.assertEqual(3, call["interval"])
        self.assertEqual(2, call["count"])
        self.assertTrue(callable(call["callback"]))
        broker.request_minute_candles.assert_not_called()

        broker.complete(self._valid_response())

        self.assertEqual(1, len(results))
        self.assertTrue(results[0].ok)
        self.assertEqual(1, provider.cache.size)
        reader.assert_not_called()

    def test_day_week_year_dispatch_with_explicit_timeframe_identity(self) -> None:
        for timeframe_key, method in (
            ("D1", "OPT10081"),
            ("W1", "OPT10082"),
            ("Y1", "OPT10094"),
        ):
            with self.subTest(timeframe_key=timeframe_key):
                self.request = ValidationRequest(
                    self.stock,
                    ValidationSettingsSnapshot(
                        {
                            "bar": {"bar_minutes": 3},
                            "validation_timeframe": {"key": timeframe_key},
                        }
                    ),
                    3,
                )
                broker = _DedicatedPeriodBroker()
                provider, broker = self._provider(
                    Mock(return_value=False),
                    broker=broker,
                )
                results = []

                self.assertIsNone(provider.request_latest(2, results.append))
                self.assertEqual(method, broker.calls[0]["method"])
                self.assertNotIn("interval", broker.calls[0])

                broker.complete(
                    {
                        "ok": True,
                        "type": "period_candles",
                        "request_id": f"period-{timeframe_key}",
                        "code": self.stock.code,
                        "name": self.stock.name,
                        "timeframe_key": timeframe_key,
                        "rows": self.rows,
                        "rows_count": len(self.rows),
                    }
                )

                self.assertTrue(results[0].ok)
                self.assertEqual(timeframe_key, results[0].snapshot.timeframe_key)
                self.assertEqual(3, results[0].snapshot.timeframe_minutes)

    def test_valid_callback_creates_snapshot_and_caches_once(self) -> None:
        provider, broker = self._provider(Mock(side_effect=(False, False)))
        results = []
        provider.request_latest(2, results.append)

        broker.complete(self._valid_response())

        self.assertEqual(1, len(results))
        result = results[0]
        self.assertTrue(result.ok)
        self.assertIsInstance(result.snapshot, ValidationHistoricalSnapshot)
        self.assertEqual(self.stock, result.snapshot.stock)
        self.assertEqual(3, result.snapshot.timeframe_minutes)
        self.assertEqual(2, result.snapshot.requested_count)
        self.assertEqual(2, result.snapshot.rows_count)
        self.assertEqual("opt10080_005930_1", result.snapshot.request_id)
        self.assertIs(
            result.snapshot,
            provider.cache.get(self.stock, 3, 2),
        )

    def test_broker_payload_mutation_cannot_change_snapshot_or_cache(self) -> None:
        provider, broker = self._provider(Mock(side_effect=(False, False)))
        results = []
        response = self._valid_response()
        expected = [dict(row) for row in self.rows]
        provider.request_latest(2, results.append)

        broker.complete(response)
        response_rows = response["rows"]
        response_rows[0]["현재가"] = "1"
        response_rows.append({"현재가": "2"})

        snapshot = results[0].snapshot
        self.assertEqual(expected, snapshot.to_rows())
        self.assertEqual(expected, provider.cache.get(self.stock, 3, 2).to_rows())

    def test_to_rows_returns_detached_mutable_copy(self) -> None:
        snapshot = ValidationHistoricalSnapshot(
            stock=self.stock,
            timeframe_minutes=3,
            requested_count=2,
            request_id="REQUEST-1",
            rows=self.rows,
        )
        first = snapshot.to_rows()
        first[0]["현재가"] = "1"
        first.append({})

        self.assertEqual(self.rows, snapshot.to_rows())
        with self.assertRaises(FrozenInstanceError):
            snapshot.rows_count = 99

    def test_response_identity_and_count_mismatches_never_cache(self) -> None:
        cases = (
            {"code": "000660"},
            {"interval": 5},
            {"rows_count": 1},
            {"request_id": ""},
            {"type": "other"},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                provider, broker = self._provider(
                    Mock(side_effect=(False, False))
                )
                results = []
                provider.request_latest(2, results.append)

                broker.complete(self._valid_response(**changes))

                self.assertEqual(1, len(results))
                self.assertFalse(results[0].ok)
                self.assertEqual(
                    REASON_INVALID_HISTORICAL_RESPONSE,
                    results[0].reason,
                )
                self.assertEqual(0, provider.cache.size)

    def test_malformed_rows_never_cache(self) -> None:
        cases = (
            None,
            {},
            ["not-object"],
            [{"not_json": {1, 2}}],
        )
        for rows in cases:
            with self.subTest(rows=rows):
                provider, broker = self._provider(
                    Mock(side_effect=(False, False))
                )
                results = []
                provider.request_latest(2, results.append)
                response = self._valid_response(
                    rows=rows,
                    rows_count=len(rows) if isinstance(rows, list) else 0,
                )

                broker.complete(response)

                self.assertEqual(REASON_INVALID_HISTORICAL_RESPONSE, results[0].reason)
                self.assertEqual(0, provider.cache.size)

    def test_broker_failure_never_caches(self) -> None:
        provider, broker = self._provider(Mock(return_value=False))
        results = []
        provider.request_latest(2, results.append)

        broker.complete({"ok": False, "error": "TR failed"})

        self.assertEqual(REASON_HISTORICAL_REQUEST_FAILED, results[0].reason)
        self.assertEqual("TR failed", results[0].error)
        self.assertEqual(0, provider.cache.size)

    def test_missing_or_raising_broker_is_fail_closed(self) -> None:
        cases = (
            (object(), REASON_BROKER_REQUESTER_UNAVAILABLE),
            (_FakeBroker(invocation_error=RuntimeError("request failed")), REASON_BROKER_REQUEST_ERROR),
        )
        for broker, reason in cases:
            with self.subTest(reason=reason):
                provider, _ = self._provider(Mock(return_value=False), broker=broker)
                results = []

                returned = provider.request_latest(2, results.append)

                self.assertEqual(reason, returned.reason)
                self.assertEqual([returned], results)
                self.assertEqual(0, provider.cache.size)

    def test_operation_change_during_tr_does_not_discard_response(self) -> None:
        reader = Mock(side_effect=AssertionError("operation reader must not be called"))
        provider, broker = self._provider(reader)
        results = []
        provider.request_latest(2, results.append)

        broker.complete(self._valid_response())

        self.assertEqual(1, len(broker.calls))
        self.assertEqual(1, len(results))
        self.assertTrue(results[0].ok)
        self.assertIsNotNone(results[0].snapshot)
        self.assertEqual(1, provider.cache.size)
        reader.assert_not_called()

    def test_cache_replaces_same_key_and_clear_works(self) -> None:
        cache = ValidationHistoricalMemoryCache()
        first = ValidationHistoricalSnapshot(
            stock=self.stock,
            timeframe_minutes=3,
            requested_count=2,
            request_id="REQUEST-1",
            rows=self.rows[:1],
        )
        latest = ValidationHistoricalSnapshot(
            stock=self.stock,
            timeframe_minutes=3,
            requested_count=2,
            request_id="REQUEST-2",
            rows=self.rows,
        )

        cache.put(first)
        cache.put(latest)

        self.assertEqual(1, len(cache))
        self.assertIs(latest, cache.get(self.stock, 3, 2))
        cache.clear()
        self.assertEqual(0, cache.size)
        self.assertIsNone(cache.get(self.stock, 3, 2))

    def test_cache_presence_does_not_skip_fresh_broker_request(self) -> None:
        cache = ValidationHistoricalMemoryCache()
        cache.put(
            ValidationHistoricalSnapshot(
                stock=self.stock,
                timeframe_minutes=3,
                requested_count=2,
                request_id="CACHED",
                rows=self.rows,
            )
        )
        provider, broker = self._provider(
            Mock(return_value=False),
            cache=cache,
        )

        provider.request_latest(2, Mock())

        self.assertEqual(1, len(broker.calls))

    def test_provider_instances_do_not_share_default_cache(self) -> None:
        first, _ = self._provider(Mock(return_value=False))
        second, _ = self._provider(Mock(return_value=False))
        self.assertIsNot(first.cache, second.cache)

    def test_source_excludes_production_mock_evaluator_and_disk_dependencies(self) -> None:
        source_path = (
            Path(__file__).resolve().parents[1]
            / "routines"
            / "지표추종매매"
            / "routine_validation_historical.py"
        )
        tree = ast.parse(source_path.read_text(encoding="utf-8-sig"))
        imported_modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)
        forbidden = (
            "kiwoom_api",
            "kiwoom_candle_adapter",
            "candle_manager",
            "stock_repository",
            "gui_market_data_host",
            "gui_auto_trade_",
            "routine_signal_",
            "order",
            "execution",
            "mock_validation",
            "indicator_engine",
            "condition_engine",
            "routine_macd_engine",
        )
        for module_name in imported_modules:
            for fragment in forbidden:
                with self.subTest(module=module_name, fragment=fragment):
                    self.assertNotIn(fragment, module_name.lower())


if __name__ == "__main__":
    unittest.main()
