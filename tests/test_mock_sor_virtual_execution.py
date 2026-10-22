"""Synthetic broker-attested SOR contract tests; no live SOR feed is assumed."""

from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock

from mock_validation_contract import payload_hash
from mock_validation_host import MockValidationHost
from mock_validation_market_data import normalize_mock_orderbook_snapshot
from mock_validation_ui_actions import MockValidationUIActions
from kiwoom_api import REALTIME_EXECUTION_TYPE
from kiwoom_realtime_fids import REALTIME_ASK_PRICE_FIDS, REALTIME_BID_PRICE_FIDS
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

    def _start(self, *, side="BUY"):
        rules = _buy_rules(qty=2) if side == "BUY" else _sell_rules(qty=2)
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
        self.assertTrue(self.host.accept_orderbook(book))
        self.assertTrue(self.host.accept_trade(trade))
        market = self.host.market_store.market_snapshot("005930")
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

    def test_no_verifier_stays_closed_even_with_sor_labels(self):
        session_id = self._start()
        self._evidence()
        self.host._sor_realtime_source_verifier = None
        self.host.process_due_cycles(as_of=NOW)
        self.assertEqual([], self.host.repository.read_session(session_id)["fills"])
        self.api.send_order.assert_not_called()

    def test_existing_plain_broker_callbacks_carry_no_sor_proof(self):
        api = _api()
        api.sync_realtime_monitoring_registration(["005930"])
        api.sync_mock_orderbook_registration(["005930"])
        api._on_receive_real_data("005930", "주식호가잔량", "")
        api._on_receive_real_data("005930", REALTIME_EXECUTION_TYPE, "")
        self.assertEqual("", api.mock_orderbook_received.values[0].source_proof)
        self.assertNotIn("source_proof", api.realtime_shadow_tick_received.values[0])

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
