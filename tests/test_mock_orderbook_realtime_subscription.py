# -*- coding: utf-8 -*-

from __future__ import annotations

from collections import deque
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

from kiwoom_api import KiwoomApi
from kiwoom_realtime_fids import (
    REALTIME_ASK_PRICE_FIDS,
    REALTIME_ASK_QTY_FIDS,
    REALTIME_BID_PRICE_FIDS,
    REALTIME_BID_QTY_FIDS,
    REALTIME_ORDERBOOK_FIDS,
    REALTIME_SHADOW_FIDS,
    REALTIME_TOTAL_ASK_QTY_FID,
    REALTIME_TOTAL_BID_QTY_FID,
)
from kiwoom_realtime_shadow import RealtimeShadowBarBuilder
from kiwoom_screen_allocator import KiwoomScreenAllocator
from mock_validation_market_data import MockValidationMarketDataStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_SHADOW_FIDS = (20, 10, 13, 15, 12, 16, 17, 18, 30, 228)


class _Signal:
    def __init__(self) -> None:
        self.values = []
        self.callbacks = []

    def emit(self, value) -> None:
        self.values.append(value)
        for callback in tuple(self.callbacks):
            callback(value)

    def connect(self, callback) -> None:
        self.callbacks.append(callback)


class _Control:
    def __init__(self) -> None:
        self.calls = []
        self.real_values = {
            20: "101501", 10: "-1234", 13: "9876", 15: "+123",
            12: "+1.25", 16: "-1200", 17: "+1300", 18: "-1100",
            30: "-12.43", 228: "117.2", 21: "101501",
        }
        for index in range(10):
            self.real_values[REALTIME_ASK_PRICE_FIDS[index]] = str(-(1300 + index))
            self.real_values[REALTIME_BID_PRICE_FIDS[index]] = str(1299 - index)
            self.real_values[REALTIME_ASK_QTY_FIDS[index]] = str(100 + index)
            self.real_values[REALTIME_BID_QTY_FIDS[index]] = str(200 + index)
        self.real_values[REALTIME_TOTAL_ASK_QTY_FID] = "1045"
        self.real_values[REALTIME_TOTAL_BID_QTY_FID] = "2045"

    def dynamicCall(self, signature, *args):
        self.calls.append((signature, args))
        if signature.startswith("GetCommRealData"):
            return self.real_values.get(args[1], "")
        return 0


def _api(*, connected: bool = True) -> KiwoomApi:
    api = KiwoomApi.__new__(KiwoomApi)
    api._control = _Control()
    api._available = True
    api._connected = connected
    api._login_requested = False
    api._login_session_id = "SESSION-1" if connected else ""
    api._connection_epoch = 1
    api.last_login_error = 0
    api.last_login_message = "connected" if connected else "disconnected"
    api._unavailable_reason = ""
    api._screen_allocator = KiwoomScreenAllocator()
    api._realtime_shadow_builder = RealtimeShadowBarBuilder()
    api._realtime_shadow_registration = api._empty_realtime_shadow_snapshot()
    api._realtime_receive_sequence = 0
    api._mock_orderbook_registration = api._empty_mock_orderbook_snapshot()
    api._mock_orderbook_receive_sequence = 0
    api._pending_tr = {}
    api._tr_request_queue = deque()
    api.realtime_shadow_tick_received = _Signal()
    api.realtime_shadow_bar_completed = _Signal()
    api.mock_orderbook_received = _Signal()
    api.login_state_changed = _Signal()
    return api


def _calls(api: KiwoomApi, prefix: str):
    return [call for call in api._control.calls if call[0].startswith(prefix)]


def _tree_hashes(*roots: Path) -> dict[str, str]:
    return {
        str(path.relative_to(PROJECT_ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for root in roots
        if root.exists()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class MockOrderbookRealtimeSubscriptionTest(unittest.TestCase):
    def test_official_fids_are_separate_and_shadow_fids_unchanged(self) -> None:
        self.assertEqual(ORIGINAL_SHADOW_FIDS, REALTIME_SHADOW_FIDS)
        self.assertEqual(tuple(range(41, 51)), REALTIME_ASK_PRICE_FIDS)
        self.assertEqual(tuple(range(51, 61)), REALTIME_BID_PRICE_FIDS)
        self.assertEqual(tuple(range(61, 71)), REALTIME_ASK_QTY_FIDS)
        self.assertEqual(tuple(range(71, 81)), REALTIME_BID_QTY_FIDS)
        self.assertEqual(43, len(REALTIME_ORDERBOOK_FIDS))

    def test_one_stock_and_same_stock_instances_use_one_target(self) -> None:
        api = _api()
        result = api.sync_mock_orderbook_registration(
            ["005930", "A005930", "005930"],
        )
        self.assertEqual(("005930",), result["snapshot"].target_stock_codes)
        self.assertEqual(1, len(_calls(api, "SetRealReg")))
        self.assertEqual("005930", _calls(api, "SetRealReg")[0][1][1])

    def test_hundred_and_multiple_batches_follow_existing_screen_policy(self) -> None:
        api = _api()
        api.sync_mock_orderbook_registration(
            [f"{index:06d}" for index in range(1, 101)],
        )
        self.assertEqual(1, len(_calls(api, "SetRealReg")))
        self.assertEqual(100, len(_calls(api, "SetRealReg")[0][1][1].split(";")))

        api = _api()
        result = api.sync_mock_orderbook_registration(
            [f"{index:06d}" for index in range(1, 102)],
        )
        calls = _calls(api, "SetRealReg")
        self.assertEqual(2, len(calls))
        self.assertEqual([100, 1], [len(call[1][1].split(";")) for call in calls])
        self.assertTrue(all(batch.owner.startswith("mock_orderbook:1:") for batch in result["snapshot"].screen_batches))

    def test_unchanged_target_does_not_reregister(self) -> None:
        api = _api()
        first = api.sync_mock_orderbook_registration(["005930"])
        second = api.sync_mock_orderbook_registration(["005930"])
        self.assertTrue(first["changed"])
        self.assertFalse(second["changed"])
        self.assertEqual(1, len(_calls(api, "SetRealReg")))
        self.assertEqual(0, len(_calls(api, "SetRealRemove")))

    def test_clear_removes_only_mock_screen_and_preserves_production_registration(self) -> None:
        api = _api()
        api.sync_realtime_monitoring_registration(["005930"])
        production = api.realtime_shadow_registration_snapshot()
        production_screen = production.screen_batches[0].screen_no
        api.sync_mock_orderbook_registration(["005930"])
        mock_screen = api.mock_orderbook_registration_snapshot().screen_batches[0].screen_no
        self.assertNotEqual(production_screen, mock_screen)
        api._control.calls.clear()

        api.clear_mock_orderbook_registration(remove_from_broker=True)

        self.assertEqual(production, api.realtime_shadow_registration_snapshot())
        self.assertTrue(api._screen_allocator.is_leased(production_screen))
        self.assertEqual([(mock_screen, "ALL")], [call[1] for call in _calls(api, "SetRealRemove")])

    def test_orderbook_event_emits_mock_snapshot_only(self) -> None:
        api = _api()
        api.sync_mock_orderbook_registration(["005930"])
        api._control.calls.clear()
        with patch("kiwoom_api.commit_realtime_primary_bar_for_stock") as commit:
            api._on_receive_real_data("005930", "주식호가잔량", "unused")
        reads = _calls(api, "GetCommRealData")
        self.assertEqual(list(REALTIME_ORDERBOOK_FIDS), [call[1][1] for call in reads])
        self.assertEqual(1, len(api.mock_orderbook_received.values))
        self.assertEqual(10, len(api.mock_orderbook_received.values[0].asks))
        self.assertEqual([], api.realtime_shadow_tick_received.values)
        commit.assert_not_called()

    def test_trade_and_unrelated_types_keep_existing_behavior(self) -> None:
        api = _api()
        api.sync_realtime_monitoring_registration(["005930"])
        api.sync_mock_orderbook_registration(["005930"])
        api._control.calls.clear()
        api._on_receive_real_data("005930", "주식체결", "")
        self.assertEqual(1, len(api.realtime_shadow_tick_received.values))
        self.assertEqual([], api.mock_orderbook_received.values)
        api._on_receive_real_data("005930", "업종지수", "")
        self.assertEqual(1, len(api.realtime_shadow_tick_received.values))
        self.assertEqual([], api.mock_orderbook_received.values)

    def test_signals_deliver_shared_trade_and_orderbook_to_mock_store(self) -> None:
        api = _api()
        api.sync_realtime_monitoring_registration(["005930"])
        registration = api.sync_mock_orderbook_registration(["005930"])["snapshot"]
        store = MockValidationMarketDataStore()
        store.apply_registration_snapshot(registration)
        api.mock_orderbook_received.connect(store.accept_orderbook)
        api.realtime_shadow_tick_received.connect(store.accept_trade)

        api._on_receive_real_data("005930", "주식호가잔량", "")
        api._on_receive_real_data("005930", "주식체결", "")

        market = store.market_snapshot("005930")
        self.assertIsNotNone(market)
        self.assertEqual(1234, market.trade.current_price)
        self.assertEqual(1300, market.orderbook.asks[0].price)

    def test_disconnect_invalidates_and_reconnect_requires_new_identity(self) -> None:
        api = _api()
        api.sync_mock_orderbook_registration(["005930"])
        api._invalidate_login_session(reason="test", emit=True, increment_epoch=True)
        self.assertFalse(api.mock_orderbook_registration_snapshot().active)
        self.assertEqual(2, api._connection_epoch)
        api._connected = True
        api._login_session_id = "SESSION-2"
        current = api.sync_mock_orderbook_registration(["005930"])["snapshot"]
        self.assertEqual((2, "SESSION-2"), (
            current.connection_epoch,
            current.login_session_id,
        ))

    def test_registration_and_receive_use_no_tr_sendorder_or_production_writes(self) -> None:
        roots = tuple(
            PROJECT_ROOT / name
            for name in ("runtime", "stocks", "routine_instances", "performance_ledger")
        )
        before = _tree_hashes(*roots)
        api = _api()
        api.sync_mock_orderbook_registration(["005930"])
        api._on_receive_real_data("005930", "주식호가잔량", "")
        after = _tree_hashes(*roots)
        signatures = [call[0] for call in api._control.calls]
        self.assertEqual(before, after)
        self.assertFalse(any(name.startswith("CommRqData") for name in signatures))
        self.assertFalse(any(name.startswith("CommKwRqData") for name in signatures))
        self.assertFalse(any(name.startswith("SendOrder") for name in signatures))


if __name__ == "__main__":
    unittest.main()
