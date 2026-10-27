from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import gui_stock_data
import stock_instance_day_projection
import stock_assignment_registration_service
import gui_market_data_host
import gui_indicator_follow_signal_validation_v5_flow as v5_flow
import gui_stock_instance_chart_window as mini_chart
import mock_validation_host
import auto_candle_refresh
from gui_indicator_follow_signal_validation_v5_flow import ValidationStockRef
from stock_repository import StockRepository

from stock_market_source_plan import (
    initialize_stock_market_source_plan,
    read_stock_market_source_plan,
    source_for_time,
)


KST = timezone(timedelta(hours=9))
DAY = datetime(2026, 10, 26, 8, 0, tzinfo=KST)


class StockMarketSourcePlanTests(unittest.TestCase):
    def test_native_m1_1535_special_is_displayed_but_not_signal_input(self) -> None:
        from stock_market_source_plan import split_native_closing_price_candles

        rows = [
            {"time": "20261020153400", "close": 100},
            {"time": "20261020153500", "close": 100},
            {"time": "20261020154000", "close": 101},
        ]
        calculation, display = split_native_closing_price_candles(
            rows, timeframe_minutes=1, market_data_identity="005930_AL",
            market_source="INTEGRATED",
        )
        self.assertEqual(["20261020153400", "20261020154000"],
                         [row["time"] for row in calculation])
        self.assertEqual([row["time"] for row in rows], [row["time"] for row in display])
        self.assertEqual("BROKER_AL_NATIVE_1535_CLOSING_PRICE",
                         display[1]["display_only_special"])
        self.assertNotIn("display_only_special", rows[1])

    def test_native_m3_1535_is_ambiguous_and_normal_m3_is_preserved(self) -> None:
        from stock_market_source_plan import split_native_closing_price_candles

        normal = {"time": "20261020153300", "close": 100}
        suspect = {"time": "20261020153500", "close": 101}
        with self.assertRaisesRegex(ValueError, "SPECIAL_BAR_AMBIGUOUS"):
            split_native_closing_price_candles(
                [normal, suspect], timeframe_minutes=3,
                market_data_identity="005930_AL", market_source="INTEGRATED",
            )
        calculation, display = split_native_closing_price_candles(
            [normal], timeframe_minutes=3,
            market_data_identity="005930_AL", market_source="INTEGRATED",
        )
        self.assertEqual([normal], calculation)
        self.assertEqual([normal], display)

    def test_v5_integrated_native_display_admits_1535_without_signal_claim(self) -> None:
        self.assertTrue(v5_flow.IndicatorFollowSignalValidationV5Flow._integrated_display_time_supported(
            {"time": "20261020153500"}, 1,
        ))

    def test_v5_native_m1_snapshot_preserves_special_provenance(self) -> None:
        from tests.test_display_only_integrated_history import _opt10080_row

        snapshot = v5_flow.ValidationHistoricalSnapshot(
            stock=ValidationStockRef("005930", "Samsung"),
            timeframe_minutes=1, timeframe_key="M1", requested_count=1,
            request_id="native-m1", rows=[_opt10080_row("20261020153500", 100)],
            market_data_identity="005930_AL", market_source="INTEGRATED",
        )
        rows, _ = v5_flow.project_validation_candles(
            snapshot, as_of=DAY.replace(hour=20),
        )
        self.assertEqual("BROKER_AL_NATIVE_1535_CLOSING_PRICE",
                         rows[0]["display_only_special"])

    def test_v5_verified_pool_keeps_special_on_display_axis_only(self) -> None:
        stock = ValidationStockRef("005930", "Samsung")
        request = SimpleNamespace(
            stock=stock, timeframe_key="M1", timeframe_minutes=1,
            settings_snapshot=SimpleNamespace(to_dict=lambda: {}),
        )
        flow = v5_flow.IndicatorFollowSignalValidationV5Flow.__new__(
            v5_flow.IndicatorFollowSignalValidationV5Flow,
        )
        flow._integrated_display_sidecars = {}
        flow._historical_count = 5000
        flow._validation_source_by_code = {stock.code: True}
        pool = {
            "stock": stock, "timeframe_key": "M1", "timeframe_minutes": 1,
            "market_data_identity": "005930_AL", "market_source": "INTEGRATED",
            "request_id": "native-m1", "candles": [
                {"time": stamp, "open": 100, "high": 100, "low": 100,
                 "close": 100, "volume": 10}
                for stamp in ("20261020153400", "20261020153500", "20261020154000")
            ],
        }
        session = SimpleNamespace(request=request)
        view = flow._validation_pool_for_session(session, pool)
        self.assertEqual(["20261020153400", "20261020154000"],
                         [row["time"] for row in view["candles"]])
        self.assertEqual("BROKER_AL_NATIVE_1535_CLOSING_PRICE",
                         view["display_candles"][1]["display_only_special"])
        self.assertIs(view["display_only_integrated"], True)
        normal_base, _deficit = flow._normal_base_history_view(session, pool, 3)
        self.assertEqual(["20261020153400", "20261020154000"],
                         [row["time"] for row in normal_base["candles"]])
        with_display, _deficit = flow._normal_base_history_view(
            session, pool, 3, view,
        )
        self.assertEqual(["20261020153400", "20261020154000"],
                         [row["time"] for row in with_display["candles"]])

    def test_live_forming_1535_special_fails_closed(self) -> None:
        at = datetime(2026, 10, 20, 15, 35, tzinfo=KST)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            initialize_stock_market_source_plan(
                root, "005930", "Samsung", trading_day=at.date(),
                decided_at=at.replace(hour=8), nxt_available=True,
            )
            with patch.object(gui_market_data_host, "PROJECT_ROOT", root):
                result = gui_market_data_host.MarketDataHost.project_broker_sor_m1_calculation(
                    SimpleNamespace(), stock_code="005930",
                    rules={"bar": {"bar_minutes": 1}},
                    projection_request={"projection": "FORMING_BASE_BAR"},
                    as_of=at,
                )
            self.assertFalse(result["available"])
            self.assertEqual("SOR_SPECIAL_BAR_AMBIGUOUS", result["availability_state"])

    def test_authenticated_al_m1_ingress_tags_special_without_dropping_display(self) -> None:
        from tests.test_display_only_integrated_history import (
            DisplayOnlyMarketDataHostTest, _opt10080_row,
        )

        at = datetime(2026, 10, 20, 15, 42, tzinfo=KST)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            initialize_stock_market_source_plan(
                root, "005930", "Samsung", trading_day=at.date(),
                decided_at=at.replace(hour=8), nxt_available=True,
            )
            host = DisplayOnlyMarketDataHostTest()._host()
            rows = [_opt10080_row(f"20261020{clock}00", 100)
                    for clock in ("1534", "1535", "1540")]
            with patch.object(gui_market_data_host, "PROJECT_ROOT", root), patch.object(
                gui_market_data_host.MarketDataHost, "direct_timeframe_requirements",
                return_value={1: 3},
            ):
                installed = gui_market_data_host.MarketDataHost.install_display_timeframe_candles(
                    host, "005930", 1, rows, as_of=at,
                    market_data_identity="005930_AL", market_source="INTEGRATED",
                    display_only=True, connection_epoch=1, login_session_id="SESSION-1",
                )
            self.assertTrue(installed["ok"], installed)
            stored = host._display_timeframe_candles[("005930", 1)]
            self.assertEqual(3, len(stored))
            self.assertEqual("BROKER_AL_NATIVE_1535_CLOSING_PRICE",
                             stored[1]["display_only_special"])

    def test_live_calculation_source_skips_special_native_m1(self) -> None:
        at = datetime(2026, 10, 20, 15, 41, tzinfo=KST)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            initialize_stock_market_source_plan(
                root, "005930", "Samsung", trading_day=at.date(),
                decided_at=at.replace(hour=8), nxt_available=True,
            )
            rows = [
                {"bar_time": f"2026-10-20T{clock}:00+09:00",
                 "open": 100, "high": 100, "low": 100, "close": 100,
                 "volume": 10, "timeframe_minutes": 1, "is_complete": True,
                 "trade_date": "2026-10-20", "session": session,
                 "display_only": True, "market_data_identity": "005930_AL",
                 "market_source": "INTEGRATED"}
                for clock, session in (("15:34", "AL_TRANSITION"),
                                       ("15:35", "AL_TRANSITION"),
                                       ("15:40", "NXT_AFTER"))
            ]
            with patch.object(gui_market_data_host, "PROJECT_ROOT", root), patch.object(
                gui_market_data_host.MarketDataHost, "_current_display_candles",
                return_value=tuple(rows),
            ), patch.object(gui_market_data_host, "project_candle_supply",
                            return_value={"available": True, "candles": [rows[0], rows[2]],
                                          "forming_included": False}) as project, patch.object(
                gui_market_data_host.MarketDataHost, "_current_display_session_identity",
                return_value=(1, "SESSION-1"),
            ):
                result = gui_market_data_host.MarketDataHost.project_broker_sor_m1_calculation(
                    SimpleNamespace(), stock_code="005930",
                    rules={"bar": {"bar_minutes": 1}},
                    projection_request={"projection": "COMPLETED_TIMEFRAME"},
                    as_of=at,
                )
            self.assertEqual(1, project.call_count)
            self.assertEqual(["15:34", "15:40"],
                             [row["bar_time"][11:16] for row in project.call_args.args[0]])
            self.assertTrue(result["available"], result)
            self.assertEqual(["15:34", "15:35", "15:40"],
                             [row["bar_time"][11:16] for row in result["display_candles"]])

    def test_registration_blocks_unknown_before_creating_stock(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = StockRepository(root)
            with patch.object(gui_stock_data, "stock_repository_factory", return_value=repo), patch.object(
                gui_stock_data, "load_stock_library_snapshot",
                return_value=gui_stock_data.StockLibraryLoadSnapshot("READY", "TEST", ({"code": "005930", "nxt_available": None},)),
            ):
                self.assertFalse(gui_stock_data.append_base_stock("005930", "Samsung"))
            self.assertFalse(repo.resolve_stock_dir("005930", "Samsung").exists())

    def test_projection_reads_frozen_plan_after_library_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            initialize_stock_market_source_plan(root, "005930", "Samsung", trading_day=DAY.date(), decided_at=DAY, nxt_available=True)
            with patch.object(stock_instance_day_projection, "load_stock_library_snapshot", side_effect=AssertionError("late library read")):
                result = stock_instance_day_projection.chart_market_session_projection("005930", root, trading_day=DAY.date())
                with patch.object(stock_instance_day_projection, "datetime") as clock:
                    clock.now.return_value = DAY
                    self.assertIs(True, stock_instance_day_projection._stock_nxt_availability("005930", root))
            self.assertIs(result["nxt_available"], True)
            self.assertEqual("SOR", result["source_plan"]["sessions"][2]["source"])

    def test_existing_assignment_blocks_unknown_before_assignment_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            StockRepository(root).ensure_stock_folder("005930", "Samsung")
            with patch.object(gui_stock_data, "load_stock_library_snapshot", return_value=gui_stock_data.StockLibraryLoadSnapshot("FAILED", "TEST", ())), patch.object(
                stock_assignment_registration_service, "execute_assignment_change",
                side_effect=AssertionError("assignment started"),
            ):
                result = stock_assignment_registration_service.register_unassigned_stock_to_instance(
                    root, "005930", "Samsung", instance_id="I1", instance_name="Instance",
                    definition_id="D1", routine_type="TEST",
                )
            self.assertFalse(result.success)
            self.assertEqual("MARKET_SOURCE_PLAN_UNAVAILABLE", result.status)

    def test_runtime_readers_do_not_requery_library(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            initialize_stock_market_source_plan(root, "005930", "Samsung", trading_day=DAY.date(), decided_at=DAY, nxt_available=True)
            with patch.object(gui_market_data_host, "PROJECT_ROOT", root, create=True), patch.object(
                gui_market_data_host, "load_stock_library_snapshot", side_effect=AssertionError("market late lookup")
            ), patch.object(v5_flow, "PROJECT_ROOT", root), patch.object(
                v5_flow, "load_stock_library_snapshot", side_effect=AssertionError("v5 late lookup")
            ), patch.object(gui_market_data_host, "datetime") as market_clock, patch.object(v5_flow, "datetime") as v5_clock:
                market_clock.now.return_value = DAY
                v5_clock.now.return_value = DAY
                self.assertEqual(("005930",), gui_market_data_host.MarketDataHost._nxt_eligible_targets(("005930",)))
                self.assertIs(True, v5_flow.IndicatorFollowSignalValidationV5Flow._verified_nxt_validation_stock(ValidationStockRef("005930", "Samsung")))

    def test_mini_source_uses_frozen_session_during_nxt_closing_price(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            plan = initialize_stock_market_source_plan(Path(temp), "005930", "Samsung", trading_day=DAY.date(), decided_at=DAY, nxt_available=True)
            self.assertEqual("SOR", mini_chart._live_price_market_source(source_plan=plan, now_dt=DAY.replace(hour=15, minute=20)))
            self.assertEqual("SOR", mini_chart._live_price_market_source(source_plan=plan, now_dt=DAY.replace(hour=15, minute=30)))
            self.assertEqual((None, None, []), mini_chart.StockInstanceChartWindow._chart_display_time_range(DAY.date().isoformat(), None, []))

    def test_production_price_evidence_uses_frozen_eligibility(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            initialize_stock_market_source_plan(root, "005930", "Samsung", trading_day=DAY.date(), decided_at=DAY, nxt_available=False)
            session = SimpleNamespace(connected=True, connection_epoch=7, login_session_id="login")
            host = SimpleNamespace(
                kiwoom_api=SimpleNamespace(broker_session_snapshot=lambda: session),
                _realtime_shadow_session_identity=(7, "login"),
                high_resolution_market_state=lambda code: None,
                nxt_display_live_price_state=lambda code: None,
            )
            with patch.object(gui_market_data_host, "PROJECT_ROOT", root), patch.object(
                gui_market_data_host, "stock_nxt_availability", side_effect=AssertionError("late lookup")
            ), patch.object(gui_market_data_host, "select_production_current_price_evidence", return_value="observed") as select:
                result = gui_market_data_host.MarketDataHost.production_current_price_evidence(host, "005930", now_dt=DAY)
            self.assertEqual("observed", result)
            self.assertIs(select.call_args.kwargs["nxt_available"], False)

    def test_mock_stops_without_same_frozen_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            host = mock_validation_host.MockValidationHost.__new__(mock_validation_host.MockValidationHost)
            host.project_root = Path(temp)
            host.repository = SimpleNamespace(read_session=lambda session_id: document)
            document = {"session": {"stock_code": "005930", "validation_session_id": "S1"}, "reference_snapshot": {"calculation_market_source": "KRX"}}
            with patch.object(host, "_review_error") as review:
                result = host._process_stock(document, DAY)
            self.assertIs(result, document)
            review.assert_called_once_with(document, "MARKET_SOURCE_PLAN_UNAVAILABLE")

    def test_sor_calculation_requires_matching_frozen_plan_before_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(gui_market_data_host, "PROJECT_ROOT", Path(temp)):
                result = gui_market_data_host.MarketDataHost.project_broker_sor_m1_calculation(
                    SimpleNamespace(), stock_code="005930", rules={"bar": {"bar_minutes": 3}},
                    projection_request={"projection": "COMPLETED_TIMEFRAME"}, as_of=DAY.replace(hour=9),
                )
            self.assertFalse(result["available"])
            self.assertEqual("MARKET_SOURCE_PLAN_UNAVAILABLE", result["availability_state"])

    def test_sor_calculation_does_not_consume_native_nxt_pre_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            initialize_stock_market_source_plan(root, "005930", "Samsung", trading_day=DAY.date(), decided_at=DAY, nxt_available=True)
            with patch.object(gui_market_data_host, "PROJECT_ROOT", root):
                result = gui_market_data_host.MarketDataHost.project_broker_sor_m1_calculation(
                    SimpleNamespace(), stock_code="005930", rules={"bar": {"bar_minutes": 1}},
                    projection_request={"projection": "COMPLETED_TIMEFRAME"}, as_of=DAY.replace(minute=20),
                )
            self.assertEqual("MARKET_SOURCE_SESSION_MISMATCH", result["availability_state"])
            with patch.object(gui_market_data_host, "PROJECT_ROOT", root), patch.object(gui_market_data_host, "datetime") as clock, patch.object(
                gui_market_data_host.MarketDataHost, "direct_timeframe_requirements", side_effect=AssertionError("pre-session SOR refresh")
            ):
                clock.now.return_value = DAY
                specs = gui_market_data_host.MarketDataHost.display_timeframe_refresh_specs(
                    SimpleNamespace(), "005930", DAY.strftime("%Y-%m-%d %H:%M"),
                )
            self.assertEqual((), specs)

    def test_display_request_rejects_supplied_true_without_frozen_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            window = SimpleNamespace(
                kiwoom_api=SimpleNamespace(
                    request_minute_candles_display_only_integrated=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("broker called")),
                    broker_session_snapshot=lambda: (_ for _ in ()).throw(AssertionError("broker session called")),
                ),
                install_display_timeframe_candles=lambda *args, **kwargs: None,
            )
            with patch.object(auto_candle_refresh, "PROJECT_ROOT", Path(temp), create=True):
                result = auto_candle_refresh.request_display_timeframe_candles_for_stock(
                    window, "005930", "Samsung", operation_cycle_minute_key="2026-10-26 09:00",
                    timeframe_minutes=3, count=1, nxt_available=True,
                )
            self.assertEqual("MARKET_SOURCE_PLAN_UNAVAILABLE", result["reason_code"])

    def test_display_request_uses_plan_then_checks_broker_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            initialize_stock_market_source_plan(root, "005930", "Samsung", trading_day=DAY.date(), decided_at=DAY, nxt_available=True)
            calls = []
            session = SimpleNamespace(connected=True, connection_epoch=3, login_session_id="session")

            def request(code, name, *, interval, count, callback, nxt_available):
                calls.append((code, interval, nxt_available))
                callback({"ok": True, "code": code, "interval": interval, "rows": [],
                          "market_data_identity": f"{code}_AL", "market_source": "INTEGRATED",
                          "display_only": True, "connection_epoch": 3, "login_session_id": "session"})
                return {"ok": True}

            window = SimpleNamespace(
                kiwoom_api=SimpleNamespace(request_minute_candles_display_only_integrated=request,
                                           broker_session_snapshot=lambda: session),
                install_display_timeframe_candles=lambda *args, **kwargs: {"ok": True},
            )
            terminals = []
            with patch.object(auto_candle_refresh, "PROJECT_ROOT", root):
                auto_candle_refresh.request_display_timeframe_candles_for_stock(
                    window, "005930", "Samsung", operation_cycle_minute_key="2026-10-26 09:00",
                    timeframe_minutes=3, count=1, nxt_available=True, on_terminal=terminals.append,
                )
            self.assertEqual([("005930", 3, True)], calls)
            self.assertTrue(terminals[0]["ok"])
            self.assertTrue(terminals[0]["install_result"]["ok"])

    def test_eligible_stock_freezes_native_broker_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = initialize_stock_market_source_plan(
                root, "005930", "Samsung", trading_day=DAY.date(),
                decided_at=DAY, nxt_available=True,
            )
            self.assertEqual("Samsung", first["stock_name"])
            self.assertEqual("005930_NX", first["sessions"][0]["market_data_identity"])
            self.assertEqual("005930_AL", first["sessions"][1]["market_data_identity"])
            self.assertEqual("005930_AL", first["sessions"][2]["market_data_identity"])
            self.assertEqual("005930_AL", first["sessions"][3]["market_data_identity"])
            self.assertEqual("005930_AL", first["sessions"][4]["market_data_identity"])
            self.assertEqual("005930_NX", source_for_time(first, DAY)["market_data_identity"])
            self.assertEqual("005930_AL", source_for_time(first, DAY.replace(hour=15, minute=40))["market_data_identity"])
            self.assertEqual("NXT", source_for_time(first, DAY)["source"])
            self.assertEqual("SOR", source_for_time(first, DAY.replace(hour=15, minute=20))["source"])
            self.assertEqual("SOR", source_for_time(first, DAY.replace(hour=15, minute=30))["source"])
            self.assertEqual("NXT_CLOSING_PRICE", source_for_time(first, DAY.replace(hour=15, minute=35))["name"])
            self.assertEqual("SOR", source_for_time(first, DAY.replace(hour=15, minute=40))["source"])
            second = initialize_stock_market_source_plan(
                root, "005930", "Samsung", trading_day=DAY.date(),
                decided_at=DAY + timedelta(hours=1), nxt_available=False,
            )
            self.assertEqual(first, second)
            self.assertEqual(first, read_stock_market_source_plan(root, "005930", DAY.date()))

    def test_ineligible_has_real_krx_auction_and_afterhours_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            plan = initialize_stock_market_source_plan(
                Path(temp), "000660", "Hynix", trading_day=DAY.date(),
                decided_at=DAY, nxt_available=False,
            )
            self.assertIsNone(source_for_time(plan, DAY))
            self.assertEqual("000660", plan["sessions"][0]["market_data_identity"])
            self.assertEqual("000660", plan["sessions"][1]["market_data_identity"])
            self.assertEqual("KRX", source_for_time(plan, DAY.replace(hour=15, minute=20))["source"])
            self.assertEqual("KRX_CLOSING", source_for_time(plan, DAY.replace(hour=15, minute=30))["name"])
            self.assertIsNone(source_for_time(plan, DAY.replace(hour=15, minute=35)))
            self.assertEqual("KRX_AFTER_CLOSE", source_for_time(plan, DAY.replace(hour=15, minute=40))["name"])
            self.assertEqual("KRX_SINGLE_PRICE", source_for_time(plan, DAY.replace(hour=16))["name"])
            self.assertIsNone(source_for_time(plan, DAY.replace(hour=18)))

    def test_boundaries_do_not_invent_preopen_or_aftermarket_candles(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sor = initialize_stock_market_source_plan(root, "005930", "Samsung", trading_day=DAY.date(), decided_at=DAY, nxt_available=True)
            krx = initialize_stock_market_source_plan(root, "000660", "Hynix", trading_day=DAY.date(), decided_at=DAY, nxt_available=False)
            self.assertEqual("NXT_PRE", source_for_time(sor, DAY.replace(hour=8, minute=49, second=59))["name"])
            for minute in (50, 59):
                self.assertIsNone(source_for_time(sor, DAY.replace(hour=8, minute=minute)))
            self.assertEqual("REGULAR", source_for_time(sor, DAY.replace(hour=9))["name"])
            self.assertIsNone(source_for_time(krx, DAY.replace(hour=8, minute=59)))
            self.assertIsNone(source_for_time(sor, DAY.replace(hour=20)))

    def test_legacy_v2_anchor_projects_new_sessions_without_json_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = initialize_stock_market_source_plan(root, "005930", "Samsung", trading_day=DAY.date(), decided_at=DAY, nxt_available=True)
            legacy = dict(plan, version=2, sessions=[dict(item) for item in plan["sessions"]])
            legacy["sessions"] = [item for item in legacy["sessions"] if item["name"] != "NXT_CLOSING_PRICE"]
            legacy["sessions"][-1]["start_time"] = "15:40:00"
            legacy["sessions"][2]["end_time"] = "15:30:00"
            paths = (root / "runtime" / "market_source_plans" / DAY.date().isoformat() / "005930.json",
                     root / "runtime" / "market_source_plans" / "anchors" / "005930.json")
            original = json.dumps(legacy, ensure_ascii=False).encode("utf-8")
            for path in paths:
                path.write_bytes(original)
            loaded = read_stock_market_source_plan(root, "005930", DAY.date())
            self.assertEqual("SOR", source_for_time(loaded, DAY.replace(hour=15, minute=35))["source"])
            self.assertEqual((original, original), tuple(path.read_bytes() for path in paths))

    def test_legacy_v1_plan_and_anchor_read_with_native_identity_without_rewriting_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = initialize_stock_market_source_plan(
                root, "005930", "Samsung", trading_day=DAY.date(),
                decided_at=DAY, nxt_available=True,
            )
            legacy = dict(plan, version=1, sessions=[dict(item) for item in plan["sessions"]
                                                      if item["name"] != "NXT_CLOSING_PRICE"])
            legacy["sessions"][0]["market_data_identity"] = "005930"
            legacy["sessions"][2]["end_time"] = "15:30:00"
            paths = (
                root / "runtime" / "market_source_plans" / DAY.date().isoformat() / "005930.json",
                root / "runtime" / "market_source_plans" / "anchors" / "005930.json",
            )
            original = json.dumps(legacy, ensure_ascii=False).encode("utf-8")
            for path in paths:
                path.write_bytes(original)
            loaded = read_stock_market_source_plan(root, "005930", DAY.date())
            self.assertEqual("005930_NX", source_for_time(loaded, DAY)["market_data_identity"])
            self.assertEqual(plan, loaded)
            self.assertEqual(plan, initialize_stock_market_source_plan(
                root, "005930", "Samsung", trading_day=DAY.date(),
                decided_at=DAY, nxt_available=False,
            ))
            projected = read_stock_market_source_plan(root, "005930", DAY.date() + timedelta(days=1))
            self.assertEqual("005930_NX", projected["sessions"][0]["market_data_identity"])
            self.assertEqual((original, original), tuple(path.read_bytes() for path in paths))

    def test_current_schema_rejects_bare_nxt_identity_without_rewriting_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = initialize_stock_market_source_plan(
                root, "005930", "Samsung", trading_day=DAY.date(),
                decided_at=DAY, nxt_available=True,
            )
            paths = (
                root / "runtime" / "market_source_plans" / DAY.date().isoformat() / "005930.json",
                root / "runtime" / "market_source_plans" / "anchors" / "005930.json",
            )
            saved = tuple(path.read_bytes() for path in paths)
            plan["sessions"][0]["market_data_identity"] = "005930"
            invalid = json.dumps(plan).encode("utf-8")
            for path, original in zip(paths, saved):
                with self.subTest(path=path):
                    path.write_bytes(invalid)
                    with self.assertRaisesRegex(ValueError, "MARKET_SOURCE_PLAN_INVALID"):
                        read_stock_market_source_plan(root, "005930", DAY.date())
                    self.assertEqual(invalid, path.read_bytes())
                    path.write_bytes(original)

    def test_unknown_or_corrupt_plan_never_becomes_krx(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(ValueError):
                initialize_stock_market_source_plan(
                    root, "005930", "Samsung", trading_day=DAY.date(),
                    decided_at=DAY, nxt_available=None,
                )
            self.assertIsNone(read_stock_market_source_plan(root, "005930", DAY.date()))
            path = root / "runtime" / "market_source_plans" / "2026-10-26" / "005930.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"nxt_available": False}), encoding="utf-8")
            with self.assertRaises(ValueError):
                read_stock_market_source_plan(root, "005930", DAY.date())
            projected = stock_instance_day_projection.chart_market_session_projection("005930", root, trading_day=DAY.date())
            self.assertEqual([], projected["market_sessions"])
            self.assertEqual("MARKET_SOURCE_PLAN_INVALID", projected["source_plan_status"])


if __name__ == "__main__":
    unittest.main()
