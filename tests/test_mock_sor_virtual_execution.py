"""Synthetic broker-attested SOR contract tests; no live SOR feed is assumed."""

from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from mock_validation_contract import payload_hash
from mock_validation_host import MockValidationHost
from mock_validation_market_data import normalize_mock_orderbook_snapshot
from mock_validation_ui_actions import MockValidationUIActions
from candle_manager import canonical_candle_content_hash
from kiwoom_api import REALTIME_EXECUTION_TYPE
from kiwoom_realtime_fids import REALTIME_ASK_PRICE_FIDS, REALTIME_BID_PRICE_FIDS, REALTIME_ORDERBOOK_TYPE
from tests.test_mock_indicator_follow_adapter import _buy_rules, _sell_rules
from tests.test_mock_validation_host_ui import NOW, _Api, _reference
from tests.test_mock_orderbook_realtime_subscription import _api
from tests.test_mock_validation_market_data import _raw_orderbook, _trade_payload


class MockSorVirtualExecutionTest(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.api = _Api()
        self.api.send_order = Mock(side_effect=AssertionError("real order reached"))
        self.verified_receipts = set()
        self.host = MockValidationHost(
            self.api, project_root=Path(temporary.name), now_factory=lambda: NOW,
            candles_provider=lambda **_: {"available": False, "candles": []},
            sor_realtime_source_verifier=lambda market: (
                market.orderbook.snapshot_identity, market.trade.snapshot_identity
            ) in self.verified_receipts,
        )
        self.addCleanup(self.host.dispose)
        self.actions = MockValidationUIActions(self.host)

    def _start(self, *, side="BUY", direct=False):
        rules = _buy_rules(qty=2) if side == "BUY" else _sell_rules(qty=2)
        if direct:
            rules["bar"] = {"bar_minutes": 3}
        reference = _reference("005930", ("A",), rules_by_instance_id={"A": rules})
        reference["calculation_market_source"] = "SOR"
        reference["snapshot_hash"] = payload_hash({k: v for k, v in reference.items() if k != "snapshot_hash"})
        created = self.actions.create_waiting_session(reference)
        session_id = created["document"]["session"]["validation_session_id"]
        self.actions.set_instance_effective_settings("005930", "A", operation_mode="CONTINUOUS")
        self.host.start_instance_operation("005930", "A", as_of=NOW)
        if side == "SELL":
            self.host.session_service.set_instance_position(
                session_id, "A", holding_qty=2, available_qty=2,
                average_price=90, realized_cost_basis=180, command_id="MC-seed-sor-sell",
            )
            before = self.host.repository.read_session(session_id)
            self.host.repository.mutate_session(
                session_id,
                lambda document: document["cycle_state_by_instance"].__setitem__(
                    "A", {"status": "resolved", "active": True,
                          "cycle_identity": "SOR-SELL-CYCLE", "confirmed_buy_round": 1,
                          "cumulative_filled_buy_amount": 180}
                ) or document,
                expected_revision=before["revision"],
            )
        if not direct:
            self.host._instance_candle_projection = lambda *_args, **_kwargs: {
                "available": True,
                "candles": [{"bar_time": NOW.isoformat(), "close": 100,
                             "volume": 10, "timeframe_minutes": 5}],
            }
        self.host.routine_adapter._evaluator = lambda *_args: {
            "signal": side, "reason": "synthetic SOR", "signal_index": 0,
        }
        return session_id

    def _evidence(self, *, side="BUY", book_source="SOR", trade_source="SOR",
                  book_identity="broker-sor-005930", trade_identity="broker-sor-005930",
                  book_age=0, trade_age=0, book_proof=None, trade_proof=None):
        values = _raw_orderbook()
        if side == "BUY":
            values[REALTIME_ASK_PRICE_FIDS[0]] = "99"
            values[REALTIME_BID_PRICE_FIDS[0]] = "98"
        book = normalize_mock_orderbook_snapshot(
            stock_code="005930", raw_values=values, connection_epoch=1,
            login_session_id="SESSION-1", receive_sequence=1,
            received_at=(NOW - timedelta(seconds=book_age)).isoformat(),
            market_source=book_source, broker_market_identity=book_identity,
            source_proof=("BROKER_SOR_REALTIME" if book_source == "SOR" else "")
            if book_proof is None else book_proof,
        )
        trade = {
            **_trade_payload(), "current_price": 99,
            "received_at": (NOW - timedelta(seconds=trade_age)).isoformat(),
            "market_source": trade_source,
            "broker_market_identity": trade_identity,
            "source_proof": ("BROKER_SOR_REALTIME" if trade_source == "SOR" else "")
            if trade_proof is None else trade_proof,
        }
        self.assertEqual(book_source == "SOR", self.host.accept_orderbook(book))
        self.assertEqual(trade_source == "SOR", self.host.accept_trade(trade))
        market = self.host.market_store.market_snapshot("005930")
        if market is not None and market.trade is not None:
            self.verified_receipts.add((market.orderbook.snapshot_identity, market.trade.snapshot_identity))

    def _assert_fill(self, side, expected_price):
        session_id = self._start(side=side)
        self._evidence(side=side)
        self.host.process_due_cycles(as_of=NOW)
        document = self.host.repository.read_session(session_id)
        self.assertTrue(document["fills"], document["orders"])
        self.assertEqual({expected_price}, {fill["price"] for fill in document["fills"]})
        self.api.send_order.assert_not_called()

    def test_attested_sor_buy_uses_sor_ask(self):
        self._assert_fill("BUY", 99)

    def test_attested_sor_sell_uses_sor_bid(self):
        self._assert_fill("SELL", 100)

    def _assert_direct_m3_virtual_position_and_marker(self, side, *, invalid=False):
        session_id = self._start(side=side, direct=True)
        self.host.routine_adapter.market_bar_projection_request = lambda _rules: {
            "projection": "COMPLETED_TIMEFRAME", "warmup_bars": 1,
            "history_target_bars": 1,
        }
        row = {
            "bar_time": (NOW - timedelta(minutes=3)).isoformat(),
            "open": 100, "high": 101, "low": 99, "close": 100,
            "volume": 10, "timeframe_minutes": 3, "is_complete": True,
            "trade_date": NOW.date().isoformat(), "session": "KRX",
            "market_source": "INTEGRATED", "market_data_identity": "005930_AL",
            "display_only": True,
        }
        source_hash = canonical_candle_content_hash([row])
        value = {
            "available": True, "stock_code": "005930", "timeframe_minutes": 3,
            "projection": "COMPLETED_TIMEFRAME", "market_source": "INTEGRATED",
            "market_data_identity": "005930_AL",
            "calculation_source": "BROKER_AL_CONFIRMED_DIRECT",
            "direct_history_source": "BROKER_AL_CONFIRMED_DIRECT",
            "source_proof": "BROKER_ROWS_NOT_INDEPENDENT_MERGE_ATTESTATION",
            "calculation_only": True, "execution_enabled": False,
            "queue_connected": False, "send_order_connected": False,
            "complete": True, "forming_included": False,
            "direct_timeframe_source": True, "available_timeframe_bars": 1,
            "source_identity": source_hash, "candles": [row],
            "direct_source_receipt": {
                "stock_code": "005930", "timeframe_key": "M3",
                "source_content_sha256": source_hash, "connection_epoch": 1,
                "session_sha256": "a" * 64, "source_as_of": NOW.isoformat(),
            },
            "calculation_as_of": NOW.isoformat(),
        }
        if invalid:
            value["candles"][0]["close"] = 101
        self.host._sor_calculation_value_reader = lambda **_kw: deepcopy(value)
        self._evidence(side=side)
        self.host.process_due_cycles(as_of=NOW)
        document = self.host.repository.read_session(session_id)
        if invalid:
            self.assertEqual([], document["fills"])
            self.assertEqual(0, document["positions"][0]["holding_qty"])
            self.assertFalse(self.host._instance_candle_projection(
                document, instance_id="A", rules={"bar": {"bar_minutes": 3}},
                settings={}, now=NOW,
                projection_request={"projection": "COMPLETED_TIMEFRAME",
                                    "warmup_bars": 1})["available"])
            self.api.send_order.assert_not_called()
            return
        self.assertEqual({99 if side == "BUY" else 100},
                         {fill["price"] for fill in document["fills"]})
        self.assertEqual(1 if side == "BUY" else 0,
                         document["positions"][0]["holding_qty"])
        from mock_validation_quick_chart import _projection
        target = SimpleNamespace(stock_code="005930", validation_session_id=session_id,
                                 routine_instance_id="A")
        chart = _projection(SimpleNamespace(mock_validation_host=self.host), target,
                            "005930", NOW.date().isoformat())
        self.assertEqual("VALID", chart["projection_status"])
        self.assertEqual([row["bar_time"]],
                         [candle["bar_time"] for candle in chart["candles"]])
        self.assertEqual(1, len(chart["buy_signal_markers" if side == "BUY"
                                      else "sell_signal_markers"]))
        self.api.send_order.assert_not_called()

        bad = deepcopy(value)
        bad["candles"][0]["close"] = 101
        self.host._sor_calculation_value_reader = lambda **_kw: bad
        blocked = self.host._instance_candle_projection(
            document, instance_id="A", rules={"bar": {"bar_minutes": 3}},
            settings={}, now=NOW,
            projection_request={"projection": "COMPLETED_TIMEFRAME", "warmup_bars": 1})
        self.assertFalse(blocked["available"])
        self.api.send_order.assert_not_called()

    def test_direct_m3_buy_reaches_virtual_position_and_quick_chart_marker(self):
        self._assert_direct_m3_virtual_position_and_marker("BUY")

    def test_direct_m3_sell_reaches_virtual_position_and_quick_chart_marker(self):
        self._assert_direct_m3_virtual_position_and_marker("SELL")

    def test_tampered_direct_m3_blocks_virtual_execution(self):
        self._assert_direct_m3_virtual_position_and_marker("BUY", invalid=True)

    def test_default_forming_request_keeps_direct_history_and_virtual_buy(self):
        session_id = self._start(direct=True)
        rows = []
        for days_back, count in ((4, 70), (3, 70), (2, 70), (1, 69), (0, 20)):
            day = NOW.date() - timedelta(days=days_back)
            for index in range(count):
                stamp = NOW.replace(year=day.year, month=day.month, day=day.day,
                                    hour=9, minute=0) + timedelta(minutes=3 * index)
                rows.append({
                    "bar_time": stamp.isoformat(), "open": 100, "high": 101,
                    "low": 99, "close": 100, "volume": 10,
                    "timeframe_minutes": 3, "is_complete": True,
                    "trade_date": day.isoformat(), "session": "KRX",
                    "market_source": "INTEGRATED", "market_data_identity": "005930_AL",
                    "display_only": True,
                })
        forming = dict(rows[-1], bar_time=NOW.isoformat(),
                       is_complete=False, display_only=False)
        direct_hash = canonical_candle_content_hash(rows)
        receipt = {
            "stock_code": "005930", "timeframe_key": "M3",
            "source_content_sha256": direct_hash, "connection_epoch": 1,
            "session_sha256": "a" * 64, "source_as_of": NOW.isoformat(),
        }
        value = {
            "available": True, "stock_code": "005930", "timeframe_minutes": 3,
            "projection": "FORMING_BASE_BAR", "market_source": "INTEGRATED",
            "market_data_identity": "005930_AL",
            "calculation_source": "BROKER_AL_CONFIRMED_M1",
            "direct_timeframe_source": True,
            "direct_history_source": "BROKER_AL_CONFIRMED_DIRECT",
            "direct_source_identity": direct_hash,
            "direct_source_receipt": receipt,
            "source_identity": canonical_candle_content_hash([{"m1": "attested"}]),
            "source_proof": "BROKER_ROWS_NOT_INDEPENDENT_MERGE_ATTESTATION",
            "calculation_only": True, "execution_enabled": False,
            "queue_connected": False, "send_order_connected": False,
            "complete": False, "forming_included": True,
            "forming_snapshot_source": "BROKER_AL_TR_FORMING_M1",
            "forming_snapshot_received_at": NOW.isoformat(),
            "sor_source_receipt": {
                "stock_code": "005930", "timeframe_key": "M3",
                "source_base_timeframe_key": "M1",
                "source_snapshot_key": "b" * 64, "connection_epoch": 1,
                "session_sha256": "a" * 64, "source_as_of": NOW.isoformat(),
                "received_at": NOW.isoformat(),
                "source_proof": "BROKER_ROWS_NOT_INDEPENDENT_MERGE_ATTESTATION",
                "independent_merge_verified": False,
            },
            "available_timeframe_bars": 300, "candles": [*rows, forming],
            "calculation_as_of": NOW.isoformat(),
        }
        self.host._sor_calculation_value_reader = lambda **_kw: deepcopy(value)
        self._evidence()
        self.host.process_due_cycles(as_of=NOW)
        document = self.host.repository.read_session(session_id)
        self.assertEqual({99}, {fill["price"] for fill in document["fills"]})
        self.assertEqual(1, document["positions"][0]["holding_qty"])
        self.api.send_order.assert_not_called()

    def test_broker_sor_callbacks_reach_virtual_fill_without_external_verifier(self):
        self.host.dispose()
        api = _api()
        api.send_order = Mock(side_effect=AssertionError("real order reached"))
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.api = api
        self.host = MockValidationHost(
            api, project_root=Path(temporary.name), now_factory=lambda: NOW,
            candles_provider=lambda **_: {"available": False, "candles": []},
        )
        self.addCleanup(self.host.dispose)
        self.actions = MockValidationUIActions(self.host)
        session_id = self._start()
        self.assertEqual(("005930",), api.mock_orderbook_registration_snapshot().sor_stock_codes)
        api._control.real_values[REALTIME_ASK_PRICE_FIDS[0]] = "99"
        api._control.real_values[REALTIME_BID_PRICE_FIDS[0]] = "98"
        with patch("kiwoom_api.datetime") as clock:
            clock.now.return_value = NOW
            api._on_receive_real_data("005930_AL", REALTIME_ORDERBOOK_TYPE, "")
            api._on_receive_real_data("005930_AL", REALTIME_EXECUTION_TYPE, "")
        self.host.process_due_cycles(as_of=NOW)
        document = self.host.repository.read_session(session_id)
        self.assertEqual({99}, {fill["price"] for fill in document["fills"]})
        api.send_order.assert_not_called()

    def test_no_verifier_stays_closed_even_with_sor_labels(self):
        session_id = self._start()
        self._evidence()
        self.host._sor_realtime_source_verifier = None
        self.host.process_due_cycles(as_of=NOW)
        self.assertEqual([], self.host.repository.read_session(session_id)["fills"])
        self.api.send_order.assert_not_called()

    def test_existing_plain_broker_callbacks_carry_no_sor_proof(self):
        api = _api()
        api.sync_mock_orderbook_registration(["005930"])
        api._on_receive_real_data("005930", "주식호가잔량", "")
        api._on_receive_real_data("005930", REALTIME_EXECUTION_TYPE, "")
        self.assertEqual("", api.mock_orderbook_received.values[0].source_proof)
        self.assertNotIn("source_proof", api.mock_execution_tick_received.values[0])

    def test_missing_mixed_and_stale_sor_evidence_blocks_virtual_fills(self):
        for evidence in (
            {}, {"book_source": "KRX"}, {"trade_source": "KRX"},
            {"trade_identity": "different-broker-stream"},
            {"book_proof": ""}, {"trade_proof": ""},
            {"book_age": 3}, {"trade_age": 3},
        ):
            with self.subTest(evidence=evidence):
                self.host.dispose()
                self.setUp()
                session_id = self._start()
                if evidence:
                    self._evidence(**evidence)
                self.host.process_due_cycles(as_of=NOW)
                document = self.host.repository.read_session(session_id)
                self.assertEqual([], document["fills"])
                self.api.send_order.assert_not_called()
