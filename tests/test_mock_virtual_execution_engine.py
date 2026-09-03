# -*- coding: utf-8 -*-

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import tempfile
import unittest

from mock_validation_contract import MockValidationError, payload_hash
from mock_validation_market_data import (
    MockMarketSnapshot,
    MockOrderbookLevel,
    MockOrderbookSnapshot,
    MockTradeSnapshot,
)
from mock_validation_repository import MockValidationRepository
from mock_validation_session_service import MockValidationSessionService
from mock_validation_virtual_execution import (
    RESULT_ACCEPTED,
    RESULT_BLOCKED,
    RESULT_NOOP,
    MockExecutionPolicy,
    MockVirtualExecutionEngine,
)


SEOUL = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 3, 10, 0, 0, tzinfo=SEOUL)
SESSION_ID = "MV-00000000000000000000000000000301"


def _levels(values):
    result = []
    for index in range(10):
        if index < len(values):
            price, qty = values[index]
        else:
            price, qty = None, None
        result.append(MockOrderbookLevel(index + 1, price, qty))
    return tuple(result)


def _book(*, asks=(), bids=(), sequence=1, received_at=NOW, epoch=1, login="LOGIN-1"):
    ask_levels = _levels(asks)
    bid_levels = _levels(bids)
    content = {"asks": [(v.price, v.quantity) for v in ask_levels], "bids": [(v.price, v.quantity) for v in bid_levels]}
    identity = f"MOB-{sequence}-{payload_hash(content)}"
    return MockOrderbookSnapshot(
        stock_code="005930", real_type="주식호가잔량", quote_time_raw="100000",
        received_at=received_at.isoformat(timespec="microseconds"),
        connection_epoch=epoch, login_session_id=login, receive_sequence=sequence,
        asks=ask_levels, bids=bid_levels, total_ask_qty=None, total_bid_qty=None,
        content_hash=payload_hash(content), snapshot_identity=identity,
    )


def _trade(*, price, qty, side, sequence, received_at=NOW, epoch=1, login="LOGIN-1"):
    signed = qty if side == "BUY" else -qty if side == "SELL" else None
    return MockTradeSnapshot(
        stock_code="005930", current_price=price, execution_price=price,
        execution_qty=qty, execution_qty_signed=signed, trade_side=side,
        execution_time="100000", market_datetime=received_at.isoformat(),
        received_at=received_at.isoformat(timespec="microseconds"),
        connection_epoch=epoch, login_session_id=login, receive_sequence=sequence,
        snapshot_identity=f"MTR-{sequence}-{side}-{price}-{qty}",
    )


def _reference():
    instances = []
    for instance_id in ("A", "B", "C"):
        rules = {"instance": instance_id, "starting_budget": 1_000_000}
        instances.append({
            "routine_instance_id": instance_id,
            "routine_definition_id": "indicator-follow",
            "routine_type": "지표추종매매",
            "rules_snapshot": rules,
            "rules_hash": payload_hash(rules),
        })
    snapshot = {
        "stock_code": "005930", "stock_name": "삼성전자",
        "stock_identity_reference": "STOCK-005930",
        "snapshot_created_at": NOW.isoformat(), "routine_instances": instances,
    }
    snapshot["snapshot_hash"] = payload_hash(snapshot)
    return snapshot


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return "MISSING"
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


class MockVirtualExecutionEngineTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = MockValidationRepository(
            Path(self.temporary.name) / "mock_validation",
            project_root=Path(self.temporary.name) / "project",
        )
        self.service = MockValidationSessionService(
            self.repository, now_factory=lambda: NOW.isoformat(timespec="microseconds"),
        )
        self.service.create_stock_session(
            reference_snapshot=_reference(), validation_session_id=SESSION_ID,
            command_id="MC-create",
        )
        self.service.start_stock_mock_session(SESSION_ID, command_id="MC-start")
        self.engine = MockVirtualExecutionEngine(self.repository, now_factory=lambda: NOW)
        self.policy = MockExecutionPolicy(1, "LOGIN-1", 2.0, 2.0)

    def submit(self, *, side="BUY", kind="MARKET", qty=1, price=None, book=None,
               instance="A", budget=1_000_000, command="MC-order", policy=None):
        return self.engine.submit_order(
            SESSION_ID, routine_instance_id=instance, side=side, order_type=kind,
            requested_qty=qty, limit_price=price, market=book,
            policy=policy or self.policy, execution_budget=budget,
            command_id=command,
        )

    def seed_position(self, instance="A", qty=100, average=90):
        self.service.set_instance_position(
            SESSION_ID, instance, holding_qty=qty, available_qty=qty,
            average_price=average, realized_cost_basis=qty * average,
            command_id=f"MC-pos-{instance}-{qty}-{average}",
        )

    def test_market_buy_one_level_full_fill(self):
        result = self.submit(qty=10, book=_book(asks=((100, 10),), bids=((99, 20),)))
        self.assertEqual(RESULT_ACCEPTED, result["status"])
        self.assertEqual(("FILLED", 10, 0), (result["order"]["state"], result["order"]["filled_qty"], result["order"]["remaining_qty"]))
        self.assertEqual([(10, 100)], [(v["qty"], v["price"]) for v in result["fills"]])

    def test_market_buy_multi_level_sweep_and_vwap(self):
        result = self.submit(qty=100, book=_book(asks=((70000, 30), (70100, 50), (70200, 100))), budget=10_000_000)
        self.assertEqual([(30, 70000), (50, 70100), (20, 70200)], [(v["qty"], v["price"]) for v in result["fills"]])
        self.assertEqual(70090, result["vwap"])

    def test_market_buy_insufficient_depth_partial_and_no_last_price_fabrication(self):
        result = self.submit(qty=100, book=_book(asks=((100, 30), (101, 30))))
        self.assertEqual(("PARTIAL_FILL", 60, 40), (result["order"]["state"], result["order"]["filled_qty"], result["order"]["remaining_qty"]))
        self.assertEqual(2, len(result["fills"]))

    def test_market_buy_never_fills_beyond_configured_budget(self):
        result = self.submit(qty=3, book=_book(asks=((100, 3),)), budget=250)
        self.assertEqual((2, 1, "PARTIAL_FILL"), (
            result["order"]["filled_qty"], result["order"]["remaining_qty"], result["order"]["state"],
        ))
        self.assertEqual(200, result["document"]["positions"][0]["realized_cost_basis"])

    def test_market_sell_one_and_multi_level(self):
        self.seed_position(qty=100, average=90)
        result = self.submit(side="SELL", qty=70, book=_book(asks=((101, 50),), bids=((100, 20), (99, 40), (98, 100))))
        self.assertEqual([(20, 100), (40, 99), (10, 98)], [(v["qty"], v["price"]) for v in result["fills"]])
        position = next(v for v in result["document"]["positions"] if v["routine_instance_id"] == "A")
        self.assertEqual((30, 30), (position["holding_qty"], position["available_qty"]))

    def test_market_sell_insufficient_depth_is_partial(self):
        self.seed_position(qty=100)
        result = self.submit(side="SELL", qty=100, book=_book(bids=((100, 25),)))
        self.assertEqual((25, 75, "PARTIAL_FILL"), (result["order"]["filled_qty"], result["order"]["remaining_qty"], result["order"]["state"]))

    def test_malformed_gap_stops_depth_conservatively(self):
        book = _book(asks=((100, 10), (None, None), (102, 100)))
        result = self.submit(qty=50, book=book)
        self.assertEqual(10, result["order"]["filled_qty"])

    def test_stale_or_wrong_session_snapshot_opens_but_does_not_fill(self):
        stale = _book(asks=((100, 10),), received_at=NOW - timedelta(seconds=3))
        result = self.submit(qty=10, book=stale)
        self.assertEqual(("OPEN", 0, "MOCK_MARKET_STALE"), (result["order"]["state"], result["order"]["filled_qty"], result["order"]["matching_status"]))
        wrong = self.submit(qty=10, book=_book(asks=((100, 10),), login="OLD"), command="MC-wrong")
        self.assertEqual(0, wrong["order"]["filled_qty"])
        self.assertEqual("MOCK_MARKET_SESSION_INVALID", wrong["order"]["matching_status"])

    def test_duplicate_orderbook_snapshot_is_noop(self):
        first_book = _book(asks=((100, 3),), sequence=1)
        result = self.submit(qty=5, book=first_book)
        again = self.engine.process_orderbook(SESSION_ID, result["order"]["mock_order_id"], market=first_book, policy=self.policy, command_id="MC-again")
        self.assertEqual(RESULT_NOOP, again["status"])
        self.assertEqual(1, len(self.repository.read_session(SESSION_ID)["fills"]))

    def test_marketable_limit_buy_full_partial_and_boundary(self):
        full = self.submit(kind="LIMIT", qty=30, price=101, book=_book(asks=((100, 10), (101, 20), (102, 20))))
        self.assertEqual("FILLED", full["order"]["state"])
        partial = self.submit(kind="LIMIT", qty=40, price=101, book=_book(asks=((100, 10), (101, 20), (102, 20)), sequence=2), command="MC-limit-partial")
        self.assertEqual((30, 10), (partial["order"]["filled_qty"], partial["order"]["remaining_qty"]))
        self.assertTrue(partial["order"]["resting"])

    def test_limit_beyond_price_does_not_fill_and_initializes_queue_ahead(self):
        result = self.submit(kind="LIMIT", qty=100, price=100, book=_book(asks=((101, 20),), bids=((100, 500),)))
        self.assertEqual((0, 500, True), (result["order"]["filled_qty"], result["order"]["queue_ahead_qty"], result["order"]["resting"]))

    def test_marketable_limit_sell_is_symmetric(self):
        self.seed_position(qty=30)
        result = self.submit(side="SELL", kind="LIMIT", qty=30, price=99, book=_book(bids=((100, 10), (99, 20), (98, 20))))
        self.assertEqual([(10, 100), (20, 99)], [(v["qty"], v["price"]) for v in result["fills"]])

    def test_price_touch_alone_never_fills_resting_limit(self):
        result = self.submit(kind="LIMIT", qty=10, price=100, book=_book(asks=((101, 20),), bids=((100, 30),)))
        second = _book(asks=((101, 20),), bids=((100, 20),), sequence=2)
        progressed = self.engine.process_orderbook(SESSION_ID, result["order"]["mock_order_id"], market=second, policy=self.policy, command_id="MC-touch")
        self.assertEqual(0, progressed["order"]["filled_qty"])

    def test_price_through_turns_resting_limit_marketable(self):
        result = self.submit(kind="LIMIT", qty=10, price=100, book=_book(asks=((101, 20),), bids=((100, 30),)))
        crossed = _book(asks=((99, 7), (100, 3)), bids=((98, 50),), sequence=2)
        progressed = self.engine.process_orderbook(SESSION_ID, result["order"]["mock_order_id"], market=crossed, policy=self.policy, command_id="MC-through")
        self.assertEqual((10, "FILLED"), (progressed["order"]["filled_qty"], progressed["order"]["state"]))

    def test_queue_ahead_decrement_and_own_partial_fill(self):
        result = self.submit(kind="LIMIT", qty=100, price=100, book=_book(asks=((101, 20),), bids=((100, 300),)))
        order_id = result["order"]["mock_order_id"]
        for sequence, qty in ((1, 100), (2, 150)):
            step = self.engine.process_trade(SESSION_ID, order_id, trade=_trade(price=100, qty=qty, side="SELL", sequence=sequence), policy=self.policy, command_id=f"MC-tick-{sequence}")
            self.assertEqual(0, step["order"]["filled_qty"])
        step = self.engine.process_trade(SESSION_ID, order_id, trade=_trade(price=100, qty=80, side="SELL", sequence=3), policy=self.policy, command_id="MC-tick-3")
        self.assertEqual((30, 70, 0), (step["order"]["filled_qty"], step["order"]["remaining_qty"], step["order"]["queue_ahead_qty"]))
        step = self.engine.process_trade(SESSION_ID, order_id, trade=_trade(price=100, qty=40, side="SELL", sequence=4), policy=self.policy, command_id="MC-tick-4")
        self.assertEqual((70, 30), (step["order"]["filled_qty"], step["order"]["remaining_qty"]))

    def test_resting_sell_queue_progress_is_symmetric(self):
        self.seed_position(qty=10)
        result = self.submit(side="SELL", kind="LIMIT", qty=10, price=101, book=_book(asks=((101, 5),), bids=((100, 20),)))
        order_id = result["order"]["mock_order_id"]
        first = self.engine.process_trade(
            SESSION_ID, order_id, trade=_trade(price=101, qty=3, side="BUY", sequence=1),
            policy=self.policy, command_id="MC-sell-tick-1",
        )
        self.assertEqual((2, 0), (first["order"]["queue_ahead_qty"], first["order"]["filled_qty"]))
        second = self.engine.process_trade(
            SESSION_ID, order_id, trade=_trade(price=101, qty=4, side="BUY", sequence=2),
            policy=self.policy, command_id="MC-sell-tick-2",
        )
        self.assertEqual((0, 2), (second["order"]["queue_ahead_qty"], second["order"]["filled_qty"]))

    def test_wrong_side_unknown_and_unrelated_price_ticks_are_conservative(self):
        result = self.submit(kind="LIMIT", qty=10, price=100, book=_book(asks=((101, 20),), bids=((100, 0),)))
        order_id = result["order"]["mock_order_id"]
        for command, trade in (
            ("MC-buy-side", _trade(price=100, qty=10, side="BUY", sequence=1)),
            ("MC-unknown", _trade(price=100, qty=10, side="UNKNOWN", sequence=2)),
            ("MC-price", _trade(price=99, qty=10, side="SELL", sequence=3)),
        ):
            output = self.engine.process_trade(SESSION_ID, order_id, trade=trade, policy=self.policy, command_id=command)
            self.assertEqual(RESULT_NOOP, output["status"])
        self.assertEqual(0, self.repository.read_session(SESSION_ID)["orders"][0]["filled_qty"])

    def test_stale_and_duplicate_trade_do_not_fill_twice(self):
        result = self.submit(kind="LIMIT", qty=10, price=100, book=_book(asks=((101, 20),), bids=((100, 0),)))
        order_id = result["order"]["mock_order_id"]
        stale = _trade(price=100, qty=5, side="SELL", sequence=1, received_at=NOW - timedelta(seconds=3))
        self.assertEqual(RESULT_NOOP, self.engine.process_trade(SESSION_ID, order_id, trade=stale, policy=self.policy, command_id="MC-stale")["status"])
        fresh = _trade(price=100, qty=5, side="SELL", sequence=1)
        self.engine.process_trade(SESSION_ID, order_id, trade=fresh, policy=self.policy, command_id="MC-fresh")
        duplicate = self.engine.process_trade(SESSION_ID, order_id, trade=fresh, policy=self.policy, command_id="MC-dup")
        self.assertEqual(RESULT_NOOP, duplicate["status"])
        self.assertEqual(5, self.repository.read_session(SESSION_ID)["orders"][0]["filled_qty"])

    def test_open_partial_cancel_and_cancelled_fill_prohibition(self):
        result = self.submit(qty=10, book=_book(asks=((100, 4),)))
        order_id = result["order"]["mock_order_id"]
        pending = self.engine.request_cancel(SESSION_ID, order_id, command_id="MC-cancel-pending")
        self.assertEqual("CANCEL_PENDING", pending["order"]["state"])
        canceled = self.engine.finalize_cancel(SESSION_ID, order_id, command_id="MC-cancel-final")
        self.assertEqual(("CANCELED", 4, 6), (canceled["order"]["state"], canceled["order"]["filled_qty"], canceled["order"]["remaining_qty"]))
        later = self.engine.process_orderbook(SESSION_ID, order_id, market=_book(asks=((99, 100),), sequence=2), policy=self.policy, command_id="MC-after-cancel")
        self.assertEqual(RESULT_NOOP, later["status"])

    def test_filled_cancel_is_rejected(self):
        result = self.submit(qty=1, book=_book(asks=((100, 1),)))
        canceled = self.engine.request_cancel(SESSION_ID, result["order"]["mock_order_id"], command_id="MC-filled-cancel")
        self.assertEqual(RESULT_BLOCKED, canceled["status"])

    def test_fill_before_cancel_and_cancel_before_fill_are_deterministic(self):
        first = self.submit(kind="LIMIT", qty=10, price=100, book=_book(asks=((101, 20),), bids=((100, 0),)))
        first_id = first["order"]["mock_order_id"]
        self.engine.process_trade(SESSION_ID, first_id, trade=_trade(price=100, qty=4, side="SELL", sequence=1), policy=self.policy, command_id="MC-fill-first")
        final = self.engine.cancel_order(SESSION_ID, first_id, command_id="MC-cancel-after")
        self.assertEqual((4, 6), (final["order"]["filled_qty"], final["order"]["remaining_qty"]))

        second = self.submit(kind="LIMIT", qty=10, price=99, book=_book(asks=((101, 20),), bids=((99, 0),), sequence=2), command="MC-second")
        second_id = second["order"]["mock_order_id"]
        self.engine.cancel_order(SESSION_ID, second_id, command_id="MC-cancel-before")
        later = self.engine.process_trade(SESSION_ID, second_id, trade=_trade(price=99, qty=10, side="SELL", sequence=2), policy=self.policy, command_id="MC-fill-late")
        self.assertEqual(RESULT_NOOP, later["status"])

    def test_multiple_buy_average_price_and_cost_basis(self):
        self.submit(qty=10, book=_book(asks=((100, 10),)), command="MC-buy-1")
        second = self.submit(qty=10, book=_book(asks=((120, 10),), sequence=2), command="MC-buy-2")
        position = next(v for v in second["document"]["positions"] if v["routine_instance_id"] == "A")
        self.assertEqual((20, 110, 2200), (position["holding_qty"], position["average_price"], position["realized_cost_basis"]))

    def test_sell_realized_pnl_commission_and_tax_enabled(self):
        self.seed_position(qty=10, average=90)
        policy = MockExecutionPolicy(1, "LOGIN-1", 2, 2, commission_rate=0.001)
        result = self.submit(side="SELL", qty=5, book=_book(bids=((100, 5),)), policy=policy)
        pnl = next(v for v in result["document"]["pnl"] if v["routine_instance_id"] == "A")
        self.assertEqual((50, 0.5, 1, 48.5), (pnl["realized_pnl"], pnl["commission"], pnl["mock_tax"], pnl["net_pnl"]))

    def test_tax_disabled(self):
        disabled_id = "MV-00000000000000000000000000000302"
        self.service.end_stock_session(SESSION_ID, command_id="MC-end")
        self.service.create_stock_session(reference_snapshot=_reference(), validation_session_id=disabled_id, command_id="MC-create-2", mock_tax_enabled=False)
        self.service.start_stock_mock_session(disabled_id, command_id="MC-start-2")
        self.service.set_instance_position(disabled_id, "A", holding_qty=1, available_qty=1, average_price=90, realized_cost_basis=90, command_id="MC-pos-2")
        result = self.engine.submit_order(disabled_id, routine_instance_id="A", side="SELL", order_type="MARKET", requested_qty=1, limit_price=None, market=_book(bids=((100, 1),)), policy=self.policy, command_id="MC-sell-2")
        pnl = next(v for v in result["document"]["pnl"] if v["routine_instance_id"] == "A")
        self.assertEqual(0, pnl["mock_tax"])

    def test_oversell_and_buy_budget_are_fail_closed(self):
        self.seed_position(qty=5)
        oversell = self.submit(side="SELL", qty=6, book=_book(bids=((100, 10),)))
        self.assertEqual((RESULT_BLOCKED, "MOCK_SELLABLE_QTY_EXCEEDED"), (oversell["status"], oversell["reason"]))
        over_budget = self.submit(kind="LIMIT", qty=11, price=100, book=_book(asks=((100, 20),)), budget=1000, command="MC-over-budget")
        self.assertEqual((RESULT_BLOCKED, "MOCK_EXECUTION_BUDGET_EXCEEDED"), (over_budget["status"], over_budget["reason"]))
        event_types = [item["event_type"] for item in self.repository.read_events(SESSION_ID)]
        self.assertEqual(2, event_types.count("VIRTUAL_ORDER_BLOCKED"))

    def test_sell_uses_mock_available_quantity_as_authority(self):
        self.service.set_instance_position(
            SESSION_ID, "A", holding_qty=10, available_qty=3,
            average_price=90, realized_cost_basis=900, command_id="MC-limited-available",
        )
        result = self.submit(side="SELL", qty=4, book=_book(bids=((100, 10),)))
        self.assertEqual((RESULT_BLOCKED, "MOCK_SELLABLE_QTY_EXCEEDED"), (result["status"], result["reason"]))

    def test_new_non_crossing_book_does_not_reinflate_queue_ahead(self):
        result = self.submit(kind="LIMIT", qty=10, price=100, book=_book(asks=((101, 20),), bids=((100, 300),)))
        order_id = result["order"]["mock_order_id"]
        progressed = self.engine.process_trade(
            SESSION_ID, order_id, trade=_trade(price=100, qty=100, side="SELL", sequence=1),
            policy=self.policy, command_id="MC-queue-progress",
        )
        self.assertEqual(200, progressed["order"]["queue_ahead_qty"])
        refreshed = self.engine.process_orderbook(
            SESSION_ID, order_id,
            market=_book(asks=((101, 20),), bids=((100, 500),), sequence=2),
            policy=self.policy, command_id="MC-book-refresh",
        )
        self.assertEqual(200, refreshed["order"]["queue_ahead_qty"])

    def test_missing_buy_budget_is_blocked(self):
        result = self.submit(book=_book(asks=((100, 1),)), budget=None)
        self.assertEqual("MOCK_EXECUTION_BUDGET_UNAVAILABLE", result["reason"])

    def test_same_market_snapshot_does_not_share_virtual_liquidity_between_instances(self):
        book = _book(asks=((100, 100),))
        results = [self.submit(instance=v, qty=100, book=book, command=f"MC-{v}") for v in ("A", "B", "C")]
        self.assertEqual([100, 100, 100], [v["order"]["filled_qty"] for v in results])
        document = self.repository.read_session(SESSION_ID)
        self.assertEqual({"A": 100, "B": 100, "C": 100}, {v["routine_instance_id"]: v["holding_qty"] for v in document["positions"]})

    def test_events_are_mock_only_and_idempotent(self):
        result = self.submit(qty=1, book=_book(asks=((100, 1),)))
        again = self.submit(qty=1, book=_book(asks=((100, 1),)), command="MC-order")
        self.assertTrue(again["duplicate"])
        event_types = [v["event_type"] for v in self.repository.read_events(SESSION_ID)]
        self.assertEqual(1, event_types.count("VIRTUAL_ORDER_CREATED"))
        self.assertIn("VIRTUAL_FILL_RECORDED", event_types)
        self.assertIn("VIRTUAL_ORDER_FILLED", event_types)
        self.assertEqual(result["order"]["mock_order_id"], again["order"]["mock_order_id"])

    def test_structural_error_can_stop_stock_session_for_review(self):
        result = self.engine.escalate_structural_review(
            SESSION_ID, routine_instance_id="B", reason_code="MOCK_LEDGER_CORRUPTION",
            reason="ledger conflict", command_id="MC-review",
        )
        self.assertTrue(result["stopped"])
        self.assertEqual("REVIEW_STOPPED", result["document"]["session"]["state"])
        self.assertEqual("B", result["document"]["review"]["source_routine_instance_id"])

    def test_session_not_running_rejects_order(self):
        self.service.reset_stock_session(SESSION_ID, command_id="MC-reset")
        with self.assertRaisesRegex(MockValidationError, "MOCK_SESSION_NOT_RUNNING"):
            self.submit(book=_book(asks=((100, 1),)))

    def test_timestamps_support_future_timeout_policy(self):
        result = self.submit(kind="LIMIT", qty=1, price=99, book=_book(asks=((100, 1),), bids=((99, 0),)))
        order = result["order"]
        self.assertEqual(order["created_at"], order["accepted_at"])
        self.assertEqual(order["accepted_at"], order["mock_opened_at"])
        self.assertEqual(order["mock_opened_at"], order["last_progress_at"])

    def test_engine_operations_do_not_mutate_production_trees(self):
        project_root = Path(__file__).resolve().parents[1]
        roots = [project_root / name for name in ("runtime", "stocks", "routine_instances", "performance_ledger")]
        before = {str(path): _tree_hash(path) for path in roots}
        self.submit(qty=2, book=_book(asks=((100, 2),)))
        after = {str(path): _tree_hash(path) for path in roots}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
