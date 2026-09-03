# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import unittest

from kiwoom_realtime_fids import (
    REALTIME_ASK_PRICE_FIDS,
    REALTIME_ASK_QTY_FIDS,
    REALTIME_BID_PRICE_FIDS,
    REALTIME_BID_QTY_FIDS,
    REALTIME_ORDERBOOK_TIME_FID,
    REALTIME_TOTAL_ASK_QTY_FID,
    REALTIME_TOTAL_BID_QTY_FID,
)
from mock_validation_market_data import (
    FRESH,
    NO_DATA,
    SESSION_INVALID,
    STALE,
    MockValidationMarketDataStore,
    normalize_mock_orderbook_snapshot,
)


SEOUL = timezone(timedelta(hours=9))
RECEIVED_AT = datetime(2026, 9, 3, 10, 0, 0, tzinfo=SEOUL)


def _raw_orderbook() -> dict[int, str]:
    values = {REALTIME_ORDERBOOK_TIME_FID: "100000"}
    for index in range(10):
        values[REALTIME_ASK_PRICE_FIDS[index]] = f"-{101 + index:,}"
        values[REALTIME_BID_PRICE_FIDS[index]] = f"+{100 - index:,}"
        values[REALTIME_ASK_QTY_FIDS[index]] = f"{1_000 + index:,}"
        values[REALTIME_BID_QTY_FIDS[index]] = f"{2_000 + index:,}"
    values[REALTIME_TOTAL_ASK_QTY_FID] = "10,045"
    values[REALTIME_TOTAL_BID_QTY_FID] = "20,045"
    return values


def _book(*, epoch: int = 1, session: str = "SESSION-1", sequence: int = 1):
    return normalize_mock_orderbook_snapshot(
        stock_code="A005930",
        raw_values=_raw_orderbook(),
        connection_epoch=epoch,
        login_session_id=session,
        receive_sequence=sequence,
        received_at=RECEIVED_AT,
    )


def _registration(*, active=True, epoch=1, session="SESSION-1", targets=("005930",)):
    return {
        "active": active,
        "connection_epoch": epoch,
        "login_session_id": session,
        "target_stock_codes": targets,
    }


def _trade_payload(*, epoch=1, session="SESSION-1", sequence=1):
    return {
        "stock_code": "005930",
        "current_price": 100,
        "trade_volume_abs": 3,
        "execution_time_raw": "100000",
        "market_datetime": "2026-09-03T10:00:00+09:00",
        "received_at": "2026-09-03T10:00:00+09:00",
        "connection_epoch": epoch,
        "login_session_id": session,
        "receive_sequence": sequence,
    }


class MockValidationMarketDataTest(unittest.TestCase):
    def test_normalizes_ten_levels_in_official_level_order(self) -> None:
        snapshot = _book()
        self.assertIsNotNone(snapshot)
        self.assertEqual("005930", snapshot.stock_code)
        self.assertEqual(10, len(snapshot.asks))
        self.assertEqual(10, len(snapshot.bids))
        self.assertEqual((1, 101, 1000), (
            snapshot.asks[0].level,
            snapshot.asks[0].price,
            snapshot.asks[0].quantity,
        ))
        self.assertEqual((1, 100, 2000), (
            snapshot.bids[0].level,
            snapshot.bids[0].price,
            snapshot.bids[0].quantity,
        ))
        self.assertEqual((10045, 20045), (
            snapshot.total_ask_qty,
            snapshot.total_bid_qty,
        ))

    def test_malformed_and_empty_levels_are_unavailable_not_zero(self) -> None:
        values = _raw_orderbook()
        values[41] = "bad"
        values[61] = "-1"
        values[42] = "0"
        values[62] = ""
        snapshot = normalize_mock_orderbook_snapshot(
            stock_code="005930",
            raw_values=values,
            connection_epoch=1,
            login_session_id="SESSION-1",
            receive_sequence=1,
            received_at=RECEIVED_AT,
        )
        self.assertIsNone(snapshot.asks[0].price)
        self.assertIsNone(snapshot.asks[0].quantity)
        self.assertIsNone(snapshot.asks[1].price)
        self.assertIsNone(snapshot.asks[1].quantity)
        self.assertEqual(10, len(snapshot.asks))

    def test_snapshot_identity_is_stable_and_snapshot_is_immutable(self) -> None:
        first = _book()
        second = _book()
        third = _book(sequence=2)
        self.assertEqual(first.snapshot_identity, second.snapshot_identity)
        self.assertNotEqual(first.snapshot_identity, third.snapshot_identity)
        with self.assertRaises(FrozenInstanceError):
            first.asks[0].price = 999

    def test_same_snapshot_object_is_shared_by_multiple_instances(self) -> None:
        store = MockValidationMarketDataStore()
        store.apply_registration_snapshot(_registration())
        snapshot = _book()
        self.assertTrue(store.accept_orderbook(snapshot))
        instance_views = {
            instance_id: store.latest_orderbook("005930")
            for instance_id in ("A", "B", "C")
        }
        self.assertIs(instance_views["A"], snapshot)
        self.assertIs(instance_views["A"], instance_views["B"])
        self.assertIs(instance_views["B"], instance_views["C"])

    def test_trade_and_orderbook_form_one_read_only_market_snapshot(self) -> None:
        store = MockValidationMarketDataStore()
        store.apply_registration_snapshot(_registration())
        self.assertTrue(store.accept_orderbook(_book()))
        self.assertTrue(store.accept_trade(_trade_payload()))
        market = store.market_snapshot("005930")
        self.assertEqual(100, market.trade.current_price)
        self.assertEqual(3, market.trade.execution_qty)
        self.assertTrue(market.snapshot_identity.startswith("MMK-"))

    def test_trade_sign_maps_to_aggressor_side_without_guessing(self) -> None:
        store = MockValidationMarketDataStore()
        store.apply_registration_snapshot(_registration())
        buy = {**_trade_payload(sequence=1), "trade_volume_raw": 3}
        self.assertTrue(store.accept_trade(buy))
        self.assertEqual((3, "BUY"), (
            store.latest_trade("005930").execution_qty_signed,
            store.latest_trade("005930").trade_side,
        ))
        sell = {**_trade_payload(sequence=2), "trade_volume_raw": -4, "trade_volume_abs": 4}
        self.assertTrue(store.accept_trade(sell))
        self.assertEqual((-4, "SELL"), (
            store.latest_trade("005930").execution_qty_signed,
            store.latest_trade("005930").trade_side,
        ))
        unknown = {**_trade_payload(sequence=3), "trade_volume_abs": 5}
        self.assertTrue(store.accept_trade(unknown))
        self.assertEqual((None, "UNKNOWN"), (
            store.latest_trade("005930").execution_qty_signed,
            store.latest_trade("005930").trade_side,
        ))

    def test_freshness_exact_boundary_stale_and_no_data(self) -> None:
        store = MockValidationMarketDataStore()
        store.apply_registration_snapshot(_registration(targets=("005930", "000660")))
        self.assertEqual(NO_DATA, store.freshness(
            "000660", now=RECEIVED_AT, max_age_seconds=2,
        ).status)
        store.accept_orderbook(_book())
        self.assertEqual(FRESH, store.freshness(
            "005930", now=RECEIVED_AT + timedelta(seconds=2), max_age_seconds=2,
        ).status)
        self.assertEqual(STALE, store.freshness(
            "005930", now=RECEIVED_AT + timedelta(seconds=2, microseconds=1),
            max_age_seconds=2,
        ).status)

    def test_disconnect_and_reconnect_invalidate_old_session_snapshot(self) -> None:
        store = MockValidationMarketDataStore()
        store.apply_registration_snapshot(_registration())
        old = _book()
        store.accept_orderbook(old)
        store.apply_registration_snapshot(_registration(active=False, targets=()))
        self.assertIsNone(store.latest_orderbook("005930"))
        self.assertEqual(SESSION_INVALID, store.freshness(
            "005930", now=RECEIVED_AT, max_age_seconds=2,
        ).status)

        store.apply_registration_snapshot(_registration(epoch=2, session="SESSION-2"))
        self.assertFalse(store.accept_orderbook(old))
        current = _book(epoch=2, session="SESSION-2", sequence=1)
        self.assertTrue(store.accept_orderbook(current))
        self.assertIs(current, store.latest_orderbook("005930"))


if __name__ == "__main__":
    unittest.main()
