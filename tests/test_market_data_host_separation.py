from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from PyQt5.QtCore import QCoreApplication, QObject
from PyQt5.QtWidgets import QWidget

from candle_manager import commit_candles
from gui_auto_trade_operation_host import AutoTradeOperationHost
from gui_market_data_host import MarketDataHost
from kiwoom_market_data_authority import REALTIME_AUTHORITY


class _Signal:
    def __init__(self) -> None:
        self.callbacks = []
        self.connect_count = 0

    def connect(self, callback) -> None:
        self.connect_count += 1
        self.callbacks.append(callback)

    def emit(self, payload) -> None:
        for callback in tuple(self.callbacks):
            callback(payload)


class _Api:
    def __init__(self) -> None:
        self.bar_committed = _Signal()
        self.realtime_shadow_bar_completed = _Signal()
        self.snapshot = SimpleNamespace(
            active=True,
            connection_epoch=7,
            login_session_id="SESSION-7",
            target_stock_codes=("005930",),
        )
        self.sync_realtime_shadow_registration = Mock(return_value={
            "ok": True,
            "active": True,
            "snapshot": self.snapshot,
        })
        self.clear_realtime_shadow_registration = Mock(return_value={
            "ok": True,
            "active": False,
        })

    def realtime_shadow_registration_snapshot(self):
        return self.snapshot


class _Owner(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.kiwoom_api = _Api()


def _candle():
    return {
        "time": "20260820101500",
        "open": 100,
        "high": 105,
        "low": 98,
        "close": 103,
        "volume": 40,
    }


class MarketDataHostSeparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self) -> None:
        self.owner = _Owner()
        self.host = AutoTradeOperationHost(self.owner)
        self.market = self.host.market_data_host()

    def tearDown(self) -> None:
        self.host.shutdown()

    def test_operation_host_owns_one_market_host_using_the_same_api(self) -> None:
        self.assertIs(self.market, self.host.market_data_host())
        self.assertIsInstance(self.market, MarketDataHost)
        self.assertNotIsInstance(self.market, QWidget)
        self.assertIs(self.owner.kiwoom_api, self.market.kiwoom_api)
        self.assertIs(self.host, self.market.parent())

    def test_raw_kiwoom_signals_are_bound_exactly_once_by_market_host(self) -> None:
        self.host._bind_bar_committed_signal_once()
        self.host._bind_realtime_shadow_signals_once()
        self.market._bind_kiwoom_signals_once()
        self.assertEqual(1, self.owner.kiwoom_api.bar_committed.connect_count)
        self.assertEqual(1, self.owner.kiwoom_api.realtime_shadow_bar_completed.connect_count)
        self.assertEqual(
            1,
            self.market.receivers(self.market.canonical_bar_ready_for_operation),
        )

    def test_host_creation_adds_no_widget_api_or_polling_timer(self) -> None:
        source = inspect.getsource(MarketDataHost)
        self.assertNotIn("QAxWidget", source)
        self.assertNotIn("KiwoomApi(", source)
        self.assertNotIn("QTimer(", source)
        self.assertNotIn("QWidget", source)
        self.assertNotIn("QDialog", source)

    def test_tr_owned_event_normalizes_but_manual_event_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_Test"
            commit = commit_candles(stock_dir, [_candle()])
            ready = []
            self.market.canonical_bar_ready_for_operation.connect(ready.append)
            payload = self._tr_payload(stock_dir, commit.canonical_content_hash)
            scheduled = []
            with patch(
                "gui_market_data_host.QTimer.singleShot",
                side_effect=lambda _ms, callback: scheduled.append(callback),
            ):
                self.owner.kiwoom_api.bar_committed.emit(payload)
                scheduled.pop(0)()
                self.assertEqual([], ready)
                self.market.register_operation_candle_request(
                    "rq-owned",
                    stock_code="005930",
                    stock_name="Test",
                    stock_dir=stock_dir,
                    operation_cycle_minute_key="2026-08-20 10:16",
                )
                owned = dict(payload, rqname="rq-owned", commit_identity="owned")
                self.owner.kiwoom_api.bar_committed.emit(owned)
                scheduled.pop(0)()

            self.assertEqual(1, len(ready))
            self.assertEqual("2026-08-20 10:16", ready[0]["evaluation_tick_key"])
            self.assertEqual(stock_dir, ready[0]["stock_dir"])

    def test_realtime_ready_requires_current_primary_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_Test"
            commit = commit_candles(stock_dir, [_candle()])
            event = self._realtime_payload(stock_dir, commit.canonical_content_hash)
            with patch("gui_market_data_host.StockRepository") as repository:
                repository.return_value.resolve_stock_dir.return_value = stock_dir
                self.assertIsNone(self.market._normalize_canonical_event(event))
                state = self.market._market_data_authority
                state.ensure_session(7, "SESSION-7")
                state.sync_targets(("005930",))
                state.observe_comparison(
                    "005930", "2026-08-20 10:14",
                    status="MATCH", price_match=True,
                    volume_compared=True, volume_match=True,
                    eligible_context=True,
                )
                state.promote_at_cycle_boundary(
                    "005930",
                    readiness_valid=True,
                    unresolved_pending=False,
                    refresh_inflight=False,
                )
                state.claim_authority(
                    "005930",
                    "2026-08-20 10:15",
                    REALTIME_AUTHORITY,
                )
                ready = self.market._normalize_canonical_event(event)

            self.assertEqual("realtime_primary", ready["source"])
            self.assertEqual("2026-08-20 10:16", ready["evaluation_tick_key"])

    def test_market_and_operation_modules_keep_their_dependency_boundary(self) -> None:
        market_source = inspect.getsource(MarketDataHost)
        for forbidden in (
            "routine_signal_probe",
            "routine_signal_consumer",
            "order_queue",
            "auto_process_executable_orders_for_real_trade",
            "SendOrder",
        ):
            self.assertNotIn(forbidden, market_source)
        operation_source = inspect.getsource(AutoTradeOperationHost)
        for forbidden in (
            "SetRealReg",
            "SetRealRemove",
            "GetCommRealData",
            "RealtimeShadowBarBuilder",
            "compare_shadow_bar_to_canonical",
            "MarketDataAuthority(",
            "_pending_reconciliations",
            "_operation_candle_requests",
        ):
            self.assertNotIn(forbidden, operation_source)

    @staticmethod
    def _tr_payload(stock_dir: Path, content_hash: str):
        return {
            "event_type": "BAR_COMMITTED",
            "source": "opt10080",
            "stock_code": "005930",
            "stock_name": "Test",
            "timeframe_minutes": 1,
            "trade_date": "2026-08-20",
            "bar_time": "2026-08-20T10:15:00+09:00",
            "bar_key": "005930:1:2026-08-20T10:15:00+09:00",
            "bar_identity": "bar",
            "commit_identity": "manual",
            "canonical_content_hash": content_hash,
            "canonical_path": str(stock_dir / "candles.json"),
            "saved_count": 1,
            "rqname": "manual",
            "trcode": "opt10080",
            "connection_epoch": 7,
        }

    @staticmethod
    def _realtime_payload(stock_dir: Path, content_hash: str):
        payload = MarketDataHostSeparationTests._tr_payload(stock_dir, content_hash)
        payload.update(
            source="realtime_primary",
            rqname="",
            commit_identity="realtime",
            login_session_id="SESSION-7",
        )
        return payload


if __name__ == "__main__":
    unittest.main()
