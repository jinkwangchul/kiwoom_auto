from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import auto_candle_refresh
from candle_timeframe_aggregation import SEOUL_TIMEZONE
from gui_market_data_host import MarketDataHost
from gui_stock_data import STOCK_LIBRARY_READY
from kiwoom_market_data_authority import REALTIME_PRIMARY_SKIP
import stock_instance_day_projection
from tests.test_kiwoom_recovery_snapshot_adapter import (
    _Control,
    _Signal,
    _Timer,
    _load_kiwoom_api_module,
)


def _opt10080_row(stamp: str, price: int) -> dict[str, str]:
    return {
        "체결시간": stamp,
        "시가": str(price),
        "고가": str(price + 2),
        "저가": str(price - 2),
        "현재가": str(price + 1),
        "거래량": "10",
    }


def _calculation_minute(stamp: str, price: float) -> dict[str, object]:
    return {
        "time": stamp,
        "open": price,
        "high": price + 2,
        "low": price - 2,
        "close": price + 1,
        "volume": 10,
    }


class DisplayOnlyIntegratedApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._saved_modules = {
            name: sys.modules.get(name)
            for name in (
                "PyQt5",
                "PyQt5.QtCore",
                "PyQt5.QtWidgets",
                "PyQt5.QAxContainer",
                "kiwoom_api",
            )
        }
        cls.module = _load_kiwoom_api_module()

    @classmethod
    def tearDownClass(cls) -> None:
        for name, module in cls._saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def setUp(self) -> None:
        _Timer.callbacks.clear()
        self.control = _Control("1234567890")
        self.api = self.module.KiwoomApi.__new__(self.module.KiwoomApi)
        self.api._control = self.control
        self.api._available = True
        self.api._connected = True
        self.api._login_requested = False
        self.api._login_session_id = "KIWOOM_LOGIN_SESSION_TEST"
        self.api._connection_epoch = 1
        self.api.last_login_error = 0
        self.api.last_login_message = "login succeeded"
        self.api._unavailable_reason = ""
        self.api._pending_tr = {}
        self.api._account_funds_request_accounts = {}
        self.api.bar_committed = _Signal()

    def _set_input_calls(self) -> list[tuple[object, ...]]:
        return [
            args
            for signature, args in self.control.calls
            if signature.startswith("SetInputValue")
        ]

    def test_requires_verified_nxt_and_preserves_al_on_pagination(self) -> None:
        rejected = self.api.request_minute_candles_display_only_integrated(
            "005930",
            nxt_available=False,
        )
        self.assertFalse(rejected["ok"])
        self.assertEqual("NXT_ELIGIBILITY_NOT_VERIFIED", rejected["reason_code"])
        self.assertEqual([], self._set_input_calls())

        results: list[dict[str, object]] = []
        requested = self.api.request_minute_candles_display_only_integrated(
            "005930",
            "삼성전자",
            interval=5,
            count=3,
            callback=results.append,
            nxt_available=True,
        )
        rqname = str(requested["rqname"])
        pending = self.api._pending_tr[rqname]
        self.assertEqual("005930_AL", pending["market_data_identity"])
        self.assertEqual("INTEGRATED", pending["market_source"])
        self.assertIs(True, pending["display_only"])
        self.assertIn(("종목코드", "005930_AL"), self._set_input_calls())

        rows = [
            _opt10080_row("20261007154500", 103),
            _opt10080_row("20261007090500", 102),
            _opt10080_row("20261007083000", 101),
        ]
        original_submit = self.api._submit_governed_tr_request
        with patch.object(
            self.module,
            "commit_minute_candles_for_stock",
            side_effect=AssertionError("Production commit called"),
        ) as commit, patch.object(
            self.api,
            "_read_opt10080_rows",
            side_effect=(rows[:2], rows[2:]),
        ), patch.object(
            self.api,
            "_submit_governed_tr_request",
            wraps=original_submit,
        ) as continuation:
            self.api._on_receive_tr_data("3000", rqname, "opt10080", "", "2")
            self.assertIn(
                ("종목코드", "005930_AL"),
                continuation.call_args.kwargs["inputs"],
            )
            self.api._on_receive_tr_data("3000", rqname, "opt10080", "", "0")

        commit.assert_not_called()
        self.assertEqual(1, len(results))
        self.assertIs(True, results[0]["display_only"])
        self.assertEqual("005930_AL", results[0]["market_data_identity"])
        self.assertEqual("INTEGRATED", results[0]["market_source"])
        self.assertEqual(rows, results[0]["rows"])
        self.assertEqual(pending["request_connection_epoch"], results[0]["connection_epoch"])
        self.assertEqual(pending["request_login_session_id"], results[0]["login_session_id"])


class DisplayOnlyMarketDataHostTest(unittest.TestCase):
    @staticmethod
    def _snapshot(nxt_available: bool | None) -> SimpleNamespace:
        return SimpleNamespace(
            state=STOCK_LIBRARY_READY,
            records=({"code": "005930", "nxt_available": nxt_available},),
        )

    def _host(self) -> SimpleNamespace:
        return SimpleNamespace(
            kiwoom_api=SimpleNamespace(
                current_realtime_shadow_bar=lambda _code: None,
                broker_session_snapshot=lambda: SimpleNamespace(
                    connected=True, connection_epoch=1, login_session_id="SESSION-1"
                ),
            ),
            _candle_execution_required_by_stock={"005930": 10},
            _candle_observation_required_by_stock={},
            _candle_standby_required_by_stock={},
            _candle_execution_timeframe_requirements_by_stock={"005930": {1: 10}},
            _candle_observation_timeframe_requirements_by_stock={},
            _candle_standby_timeframe_requirements_by_stock={},
            _direct_timeframe_candles={},
            _display_timeframe_candles={},
            _display_timeframe_session_identity={},
        )

    def test_display_cache_accepts_only_integrated_evidence_without_raw_calculation(self) -> None:
        host = self._host()
        calculation_rows = [
            _calculation_minute("20261007090000", 100),
            _calculation_minute("20261007090100", 101),
        ]
        projection_args = {
            "stock_code": "005930",
            "rules": {"bar": {"bar_minutes": 1}},
            "projection_request": {"projection": "COMPLETED_TIMEFRAME"},
            "as_of": datetime(2026, 10, 7, 20, 10, tzinfo=SEOUL_TIMEZONE),
            "session_windows": (
                {"name": "KRX", "start_time": "09:00:00", "end_time": "15:30:00"},
            ),
        }
        with patch(
            "gui_market_data_host.load_stock_library_snapshot",
            return_value=self._snapshot(True),
        ), patch("gui_market_data_host.load_candles", return_value=calculation_rows), patch(
            "gui_market_data_host.StockRepository"
        ) as repository:
            repository.return_value.resolve_stock_dir.return_value = Path("unused")
            before = MarketDataHost.project_routine_candles(host, **projection_args)
            rejected = MarketDataHost.install_display_timeframe_candles(
                host,
                "005930",
                1,
                [_opt10080_row("20261007083000", 90)],
                as_of=projection_args["as_of"],
                market_data_identity="005930",
                market_source="KRX",
                display_only=True,
                connection_epoch=1,
                login_session_id="SESSION-1",
            )
            installed = MarketDataHost.install_display_timeframe_candles(
                host,
                "005930",
                1,
                [
                    _opt10080_row("20261007083000", 90),
                    _opt10080_row("20261007085000", 92),
                    _opt10080_row("20261007085500", 91),
                    _opt10080_row("20261007090500", 100),
                    _opt10080_row("20261007153000", 105),
                    _opt10080_row("20261007154500", 110),
                    _opt10080_row("20261007200000", 120),
                ],
                as_of=projection_args["as_of"],
                market_data_identity="005930_AL",
                market_source="INTEGRATED",
                display_only=True,
                connection_epoch=1,
                login_session_id="SESSION-1",
            )
            after = MarketDataHost._display_timeframe_projection(host, "005930", 1)

        self.assertFalse(rejected["ok"])
        self.assertTrue(installed["ok"], installed)
        self.assertEqual([], before["candles"])
        self.assertEqual("SOR_SOURCE_UNAVAILABLE", before["availability_state"])
        self.assertEqual(
            ["08:30:00", "09:05:00", "15:30:00", "15:45:00"],
            [str(row["bar_time"])[11:19] for row in after["display_candles"]],
        )
        self.assertEqual(
            ["NXT_PRE", "KRX", "AL_TRANSITION", "NXT_AFTER"],
            [row["session"] for row in after["display_candles"]],
        )

    def test_m1_fetch_and_retention_cover_full_700_bar_integrated_day(self) -> None:
        host = self._host()
        host._candle_observation_timeframe_requirements_by_stock = {
            "005930": {1: 700}
        }
        rows = [
            _opt10080_row(
                f"20261007{minute // 60:02d}{minute % 60:02d}00",
                100 + index,
            )
            for index, minute in enumerate(
                [
                    *range(8 * 60, 8 * 60 + 50),
                    *range(9 * 60, 15 * 60 + 30),
                    *range(15 * 60 + 40, 20 * 60),
                ]
            )
        ]
        with patch(
            "gui_market_data_host.load_stock_library_snapshot",
            return_value=self._snapshot(True),
        ):
            specs = MarketDataHost.display_timeframe_refresh_specs(
                host,
                "005930",
                "2026-10-07 20:00",
            )
            installed = MarketDataHost.install_display_timeframe_candles(
                host,
                "005930",
                1,
                rows,
                as_of=datetime(2026, 10, 7, 20, 0, tzinfo=SEOUL_TIMEZONE),
                market_data_identity="005930_AL",
                market_source="INTEGRATED",
                display_only=True,
                connection_epoch=1,
                login_session_id="SESSION-1",
            )
            fresh = MarketDataHost.display_timeframe_refresh_specs(
                host,
                "005930",
                "2026-10-07 20:00",
            )

        self.assertEqual(700, specs[0]["required_count"])
        self.assertEqual(701, specs[0]["request_count"])
        self.assertEqual(700, installed["stored_count"])
        retained = host._display_timeframe_candles[("005930", 1)]
        self.assertEqual("08:00:00", str(retained[0]["bar_time"])[11:19])
        self.assertEqual("19:59:00", str(retained[-1]["bar_time"])[11:19])
        self.assertEqual((), fresh)

    def test_session_endpoints_are_excluded_and_gap_freshness_is_stable(self) -> None:
        session_for = MarketDataHost._display_session_for_time
        cases = (
            ("08:00", "NXT_PRE"),
            ("08:49", "NXT_PRE"),
            ("08:50", ""),
            ("08:59", ""),
            ("09:00", "KRX"),
            ("15:29", "KRX"),
            ("15:30", "AL_TRANSITION"),
            ("15:39", "AL_TRANSITION"),
            ("15:40", "NXT_AFTER"),
            ("19:59", "NXT_AFTER"),
            ("20:00", ""),
        )
        for hhmm, expected in cases:
            with self.subTest(hhmm=hhmm):
                self.assertEqual(
                    expected,
                    session_for(
                        datetime.fromisoformat(
                            f"2026-10-07T{hhmm}:00+09:00"
                        )
                    ),
                )

        host = self._host()
        host._candle_execution_timeframe_requirements_by_stock = {
            "005930": {7: 1}
        }
        full_day_count = 8 + 56 + 38
        old = {"bar_time": "2026-10-06T19:56:00+09:00"}
        with patch(
            "gui_market_data_host.load_stock_library_snapshot",
            return_value=self._snapshot(True),
        ):
            host._display_timeframe_candles[("005930", 7)] = (
                *(dict(old) for _ in range(full_day_count - 1)),
                {"bar_time": "2026-10-07T08:49:00+09:00"},
            )
            host._display_timeframe_session_identity[("005930", 7)] = (1, "SESSION-1")
            pre_gap = MarketDataHost.display_timeframe_refresh_specs(
                host,
                "005930",
                "2026-10-07 08:55",
            )
            host._display_timeframe_candles[("005930", 7)] = (
                *(dict(old) for _ in range(full_day_count - 1)),
                {"bar_time": "2026-10-07T15:25:00+09:00"},
            )
            krx_gap = MarketDataHost.display_timeframe_refresh_specs(
                host,
                "005930",
                "2026-10-07 15:35",
            )

        self.assertEqual(102, MarketDataHost._full_day_display_count(7))
        self.assertEqual((), pre_gap)
        self.assertEqual((), krx_gap)

    def test_non_nxt_stock_has_no_display_refresh_spec(self) -> None:
        host = self._host()
        with patch(
            "gui_market_data_host.load_stock_library_snapshot",
            return_value=self._snapshot(False),
        ):
            self.assertEqual(
                (),
                MarketDataHost.display_timeframe_refresh_specs(
                    host,
                    "005930",
                    "2026-10-07 10:05",
                ),
            )


class DisplayOnlyRefreshFlowTest(unittest.TestCase):
    def test_display_request_has_separate_budget_and_counters(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_Test"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                '{"status":"RUNNING","trade_enabled":true}',
                encoding="utf-8",
            )
            display_calls: list[dict[str, object]] = []
            installs: list[dict[str, object]] = []
            completed: list[dict[str, object]] = []

            def display_request(code, name, **kwargs):
                display_calls.append({"code": code, "name": name, **kwargs})
                kwargs["callback"](
                    {
                        "ok": True,
                        "code": code,
                        "interval": kwargs["interval"],
                        "rows": [_opt10080_row("20261007083000", 100)],
                        "market_data_identity": f"{code}_AL",
                        "market_source": "INTEGRATED",
                        "display_only": True,
                        "connection_epoch": 1,
                        "login_session_id": "SESSION-1",
                    }
                )
                return {"ok": True, "rqname": "DISPLAY"}

            def install(code, interval, rows, **evidence):
                installs.append(
                    {
                        "code": code,
                        "interval": interval,
                        "rows": rows,
                        **evidence,
                    }
                )
                return {"ok": True, "reason_code": "DISPLAY_TIMEFRAME_READY"}

            window = SimpleNamespace(
                kiwoom_api=SimpleNamespace(
                    is_available=lambda: True,
                    is_connected=lambda: True,
                    broker_session_snapshot=lambda: SimpleNamespace(
                        connected=True, connection_epoch=1, login_session_id="SESSION-1"
                    ),
                    request_minute_candles=Mock(),
                    request_minute_candles_display_only_integrated=display_request,
                ),
                registered_operation_targets=lambda: [(stock_dir, "005930", "Test")],
                operation_candle_request_cost=lambda _code, _minute: 1,
                direct_timeframe_refresh_specs=lambda _code, _minute: (),
                display_timeframe_refresh_specs=lambda _code, _minute: (
                    {
                        "timeframe_minutes": 5,
                        "request_count": 3,
                        "nxt_available": True,
                    },
                ),
                install_display_timeframe_candles=install,
                market_data_refresh_decision=lambda _code, _minute: {
                    "decision": REALTIME_PRIMARY_SKIP,
                },
            )

            with patch.object(
                auto_candle_refresh.QTimer,
                "singleShot",
                side_effect=lambda _delay, callback: callback(),
            ):
                result = auto_candle_refresh.refresh_operation_candles(
                    window,
                    "2026-10-07 10:10",
                    on_complete=completed.append,
                )

        self.assertEqual(1, len(display_calls))
        self.assertIs(True, display_calls[0]["nxt_available"])
        self.assertEqual(1, len(installs))
        self.assertEqual(1, installs[0]["connection_epoch"])
        self.assertEqual("SESSION-1", installs[0]["login_session_id"])
        self.assertEqual(1, result["display_requested"])
        self.assertEqual(1, result["display_succeeded"])
        self.assertEqual(0, result["display_failed"])
        self.assertEqual(1, completed[0]["requested"])

    def test_mixed_refresh_never_exceeds_fifteen_request_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_Test"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                '{"status":"RUNNING","trade_enabled":true}',
                encoding="utf-8",
            )
            direct_calls: list[int] = []
            display_calls: list[int] = []

            def m1_request(code, name, **kwargs):
                kwargs["callback"]({"ok": True, "code": code, "rows": []})
                return {"ok": True, "rqname": "M1"}

            def direct_request(code, name, **kwargs):
                direct_calls.append(kwargs["interval"])
                kwargs["callback"](
                    {
                        "ok": True,
                        "code": code,
                        "interval": kwargs["interval"],
                        "rows": [_opt10080_row("20261007090000", 100)],
                    }
                )
                return {"ok": True, "rqname": f"D{kwargs['interval']}"}

            def display_request(code, name, **kwargs):
                display_calls.append(kwargs["interval"])
                kwargs["callback"](
                    {
                        "ok": True,
                        "code": code,
                        "interval": kwargs["interval"],
                        "rows": [_opt10080_row("20261007083000", 100)],
                        "market_data_identity": f"{code}_AL",
                        "market_source": "INTEGRATED",
                        "display_only": True,
                        "connection_epoch": 1,
                        "login_session_id": "SESSION-1",
                    }
                )
                return {"ok": True, "rqname": f"X{kwargs['interval']}"}

            window = SimpleNamespace(
                kiwoom_api=SimpleNamespace(
                    is_available=lambda: True,
                    is_connected=lambda: True,
                    broker_session_snapshot=lambda: SimpleNamespace(
                        connected=True, connection_epoch=1, login_session_id="SESSION-1"
                    ),
                    request_minute_candles=m1_request,
                    request_minute_candles_read_only=direct_request,
                    request_minute_candles_display_only_integrated=display_request,
                    production_candle_source_requires_reload=lambda *_args: False,
                ),
                registered_operation_targets=lambda: [(stock_dir, "005930", "Test")],
                operation_candle_request_cost=lambda _code, _minute: 21,
                direct_timeframe_refresh_specs=lambda _code, _minute: tuple(
                    {
                        "timeframe_minutes": interval,
                        "request_count": 1,
                    }
                    for interval in range(2, 12)
                ),
                display_timeframe_refresh_specs=lambda _code, _minute: tuple(
                    {
                        "timeframe_minutes": interval,
                        "request_count": 1,
                        "nxt_available": True,
                    }
                    for interval in range(12, 22)
                ),
                install_direct_timeframe_candles=lambda *_args, **_kwargs: {
                    "ok": True
                },
                install_display_timeframe_candles=lambda *_args, **_kwargs: {
                    "ok": True
                },
                candle_history_required_count=lambda _code: 1,
                market_data_refresh_decision=lambda _code, _minute: {
                    "decision": "TR_PRIMARY_REFRESH",
                },
            )

            with patch.object(
                auto_candle_refresh.QTimer,
                "singleShot",
                side_effect=lambda _delay, callback: callback(),
            ), patch.object(auto_candle_refresh, "load_candles", return_value=[]):
                result = auto_candle_refresh.refresh_operation_candles(
                    window,
                    "2026-10-07 10:10",
                )

        self.assertEqual(15, result["requested"])
        self.assertEqual(10, len(direct_calls))
        self.assertEqual(4, len(display_calls))
        self.assertEqual(6, result["display_deferred"])


class StockInstanceDisplayProjectionTest(unittest.TestCase):
    def test_chart_uses_display_rows_but_keeps_calculation_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock_dir = root / "stocks" / "005930_Test"
            stock_dir.mkdir(parents=True)
            (root / "runtime").mkdir()
            (stock_dir / "config.json").write_text(
                json.dumps({"assigned_routine_instance_id": "instance-1"}),
                encoding="utf-8",
            )
            (stock_dir / "state.json").write_text("{}", encoding="utf-8")
            rules_path = root / "rules.json"
            rules_path.write_text(
                json.dumps({"bar": {"bar_minutes": 5}}),
                encoding="utf-8",
            )
            calculation = {
                "bar_time": "2026-10-07T09:00:00+09:00",
                "open": 100,
                "high": 102,
                "low": 99,
                "close": 101,
                "volume": 10,
                "timeframe_minutes": 5,
                "is_complete": True,
            }
            display_rows = [
                {**calculation, "bar_time": "2026-10-06T08:30:00+09:00"},
                {**calculation, "bar_time": "2026-10-07T08:30:00+09:00"},
                {**calculation, "bar_time": "2026-10-07T15:45:00+09:00"},
            ]

            def provider(**_kwargs):
                return {
                    "available": True,
                    "availability_state": "AVAILABLE",
                    "candles": [calculation],
                    "timeframe_minutes": 5,
                    "available_minute_candles": 1,
                    "source_identity": "CALCULATION-HASH",
                    "display_candles": display_rows,
                    "display_metadata": {
                        "available": True,
                        "display_only": True,
                        "market_data_identity": "005930_AL",
                        "market_source": "INTEGRATED",
                    },
                }

            with patch.object(
                stock_instance_day_projection,
                "routine_instance_by_id",
                return_value=SimpleNamespace(
                    rules_path=rules_path,
                    definition_id="definition-1",
                    display_name="Instance",
                ),
            ), patch.object(
                stock_instance_day_projection,
                "routine_definition_by_id",
                return_value=object(),
            ), patch.object(
                stock_instance_day_projection,
                "load_routine_callable",
                return_value=lambda _rules: {"projection": "COMPLETED_TIMEFRAME"},
            ), patch.object(
                stock_instance_day_projection,
                "load_stock_library_snapshot",
                return_value=SimpleNamespace(
                    state=STOCK_LIBRARY_READY,
                    records=({"code": "005930", "nxt_available": True},),
                ),
            ) as library:
                projected = stock_instance_day_projection.project_stock_instance_day(
                    "005930",
                    "2026-10-07",
                    project_root=root,
                    now=datetime(2026, 10, 7, 20, 10, tzinfo=SEOUL_TIMEZONE),
                    candle_provider=provider,
                )
                library.return_value = SimpleNamespace(
                    state=STOCK_LIBRARY_READY,
                    records=({"code": "005930", "nxt_available": False},),
                )
                non_nxt = stock_instance_day_projection.project_stock_instance_day(
                    "005930",
                    "2026-10-07",
                    project_root=root,
                    now=datetime(2026, 10, 7, 20, 10, tzinfo=SEOUL_TIMEZONE),
                    candle_provider=provider,
                )

        self.assertEqual(2, len(projected["candles"]))
        self.assertEqual(
            ["08:30:00", "15:45:00"],
            [row["bar_time"][11:19] for row in projected["candles"]],
        )
        self.assertEqual("CALCULATION-HASH", projected["source_identity"])
        self.assertEqual("KRX", projected["calculation_candle_source"])
        self.assertEqual("INTEGRATED_DISPLAY_ONLY", projected["chart_candle_source"])
        self.assertTrue(projected["chart_display_only_integrated"])
        self.assertEqual(1, projected["diagnostics"]["calculation_candle_count"])
        self.assertEqual([], projected["buy_signal_markers"])
        self.assertEqual([], projected["sell_signal_markers"])
        self.assertEqual([calculation], non_nxt["candles"])
        self.assertEqual("CALCULATION_KRX", non_nxt["chart_candle_source"])
        self.assertFalse(non_nxt["chart_display_only_integrated"])


if __name__ == "__main__":
    unittest.main()
