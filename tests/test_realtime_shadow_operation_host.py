from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PyQt5.QtCore import QCoreApplication, QObject

import gui_auto_trade_timer
from gui_auto_trade_operation_host import AutoTradeOperationHost
from gui_market_data_host import MarketDataHost
from kiwoom_api import RealtimeShadowRegistrationSnapshot
from kiwoom_realtime_fids import REALTIME_SHADOW_FIDS
from kiwoom_realtime_shadow import RealtimeShadowBar


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
        self.realtime_shadow_tick_received = _Signal()
        self.sync_realtime_monitoring_registration = Mock()
        self.sync_realtime_shadow_targets = Mock()
        self.sync_realtime_shadow_registration = Mock()
        self.clear_realtime_shadow_registration = Mock(
            return_value={"ok": True, "changed": False, "active": False}
        )
        self.snapshot = RealtimeShadowRegistrationSnapshot(
            active=True,
            connection_epoch=7,
            login_session_id="SESSION-7",
            target_stock_codes=("005930",),
            shadow_target_stock_codes=("005930",),
            fid_list=tuple(REALTIME_SHADOW_FIDS),
            screen_batches=(),
            last_error="",
        )
        self.sync_realtime_shadow_registration.return_value = {
            "ok": True,
            "changed": True,
            "active": True,
            "snapshot": self.snapshot,
        }
        self.sync_realtime_shadow_targets.return_value = dict(
            self.sync_realtime_shadow_registration.return_value
        )

    def realtime_shadow_registration_snapshot(self):
        return self.snapshot


class _Owner(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.kiwoom_api = _Api()


def _shadow_payload():
    return {
        "stock_code": "005930",
        "timeframe_minutes": 1,
        "trade_date": "2026-08-20",
        "bar_time": "2026-08-20T10:15:00+09:00",
        "open": 100,
        "high": 105,
        "low": 98,
        "close": 103,
        "volume": 40,
        "volume_complete": True,
        "first_tick_time": "2026-08-20T10:15:01+09:00",
        "last_tick_time": "2026-08-20T10:15:59+09:00",
        "tick_count": 4,
        "connection_epoch": 7,
        "login_session_id": "SESSION-7",
    }


def _canonical():
    return [{
        "time": "20260820101500",
        "open": 100,
        "high": 105,
        "low": 98,
        "close": 103,
        "volume": 40,
    }]


class RealtimeShadowOperationHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self) -> None:
        self.owner = _Owner()
        self.host = AutoTradeOperationHost(self.owner)
        self.market = self.host.market_data_host()

    def tearDown(self) -> None:
        self.host.shutdown()

    def test_shadow_and_canonical_signals_bind_once_on_persistent_host(self) -> None:
        self.host._bind_realtime_shadow_signals_once()
        self.host._bind_realtime_shadow_signals_once()
        self.assertEqual(1, self.owner.kiwoom_api.realtime_shadow_bar_completed.connect_count)
        self.assertEqual(1, self.owner.kiwoom_api.realtime_shadow_tick_received.connect_count)
        self.assertEqual(1, self.owner.kiwoom_api.bar_committed.connect_count)

    def test_target_sync_uses_execution_ready_codes_only(self) -> None:
        snapshot = SimpleNamespace(execution_stock_codes=("005930", "006400"))
        result = self.host.sync_realtime_shadow_targets(snapshot)
        self.owner.kiwoom_api.sync_realtime_shadow_targets.assert_called_once_with(
            ("005930", "006400")
        )
        self.assertTrue(result["active"])

    def test_price_signal_gate_and_market_snapshots_are_thin_forwarders(self) -> None:
        self.assertFalse(self.host.price_signal_observation_enabled())
        self.assertTrue(self.host.set_price_signal_observation_enabled(True))
        state = object()
        fresh_state = object()
        snapshot = object()
        with patch.object(
            self.market,
            "high_resolution_market_state",
            return_value=state,
        ) as state_getter, patch.object(
            self.market,
            "fresh_monitoring_market_information_state",
            return_value=fresh_state,
        ) as fresh_state_getter, patch.object(
            self.market,
            "high_resolution_market_data_snapshot",
            return_value=snapshot,
        ) as snapshot_getter:
            self.assertIs(state, self.host.high_resolution_market_state("005930"))
            self.assertIs(
                fresh_state,
                self.host.fresh_monitoring_market_information_state("005930"),
            )
            self.assertIs(snapshot, self.host.high_resolution_market_data_snapshot())

        state_getter.assert_called_once_with("005930")
        fresh_state_getter.assert_called_once_with("005930")
        snapshot_getter.assert_called_once_with()

    def test_shadow_first_pends_then_later_canonical_commit_compares(self) -> None:
        scheduled = []
        comparisons = []
        self.host.realtime_shadow_comparison_completed.connect(comparisons.append)
        repository = Mock()
        repository.resolve_stock_dir.return_value = Path("C:/temp/005930_Test")

        with patch("gui_auto_trade_operation_host.QTimer.singleShot", side_effect=lambda _ms, callback: scheduled.append(callback)), patch(
            "gui_market_data_host.StockRepository", return_value=repository
        ), patch("gui_market_data_host.load_candles", side_effect=[[], _canonical()]), patch(
            "gui_market_data_host.canonical_candle_content_hash", return_value="hash"
        ):
            self.owner.kiwoom_api.realtime_shadow_bar_completed.emit(_shadow_payload())
            self.assertEqual([], comparisons)
            scheduled.pop(0)()
            self.assertEqual(1, len(self.market._pending_shadow_comparisons))
            self.owner.kiwoom_api.bar_committed.emit({
                "event_type": "BAR_COMMITTED",
                "stock_code": "005930",
                "source": "opt10080",
                "timeframe_minutes": 1,
            })
            scheduled.pop(0)()
            scheduled.pop(0)()
            scheduled.pop(0)()

        self.assertEqual("MATCH", comparisons[0]["status"])
        self.assertEqual(0, len(self.market._pending_shadow_comparisons))

    def test_canonical_first_then_shadow_later_compares(self) -> None:
        scheduled = []
        comparisons = []
        self.host.realtime_shadow_comparison_completed.connect(comparisons.append)
        repository = Mock()
        repository.resolve_stock_dir.return_value = Path("C:/temp/005930_Test")
        with patch("gui_auto_trade_operation_host.QTimer.singleShot", side_effect=lambda _ms, callback: scheduled.append(callback)), patch(
            "gui_market_data_host.StockRepository", return_value=repository
        ), patch("gui_market_data_host.load_candles", return_value=_canonical()), patch(
            "gui_market_data_host.canonical_candle_content_hash", return_value="hash"
        ):
            self.owner.kiwoom_api.bar_committed.emit({
                "event_type": "BAR_COMMITTED",
                "stock_code": "005930",
                "source": "opt10080",
                "timeframe_minutes": 1,
            })
            scheduled.pop(0)()
            self.assertEqual([], scheduled)
            self.owner.kiwoom_api.realtime_shadow_bar_completed.emit(_shadow_payload())
            scheduled.pop(0)()
            scheduled.pop(0)()

        self.assertEqual("MATCH", comparisons[0]["status"])

    def test_stale_session_shadow_bar_is_ignored(self) -> None:
        payload = _shadow_payload()
        payload["login_session_id"] = "OLD"
        self.market._realtime_shadow_trigger_queue.append(payload)
        with patch.object(self.market, "_compare_or_pend_realtime_shadow_bar") as compare:
            self.market._drain_realtime_shadow_events()
        compare.assert_not_called()

    def test_shadow_comparison_has_no_trading_side_effect_calls(self) -> None:
        source = __import__("inspect").getsource(
            MarketDataHost._compare_or_pend_realtime_shadow_bar
        )
        for forbidden in (
            "probe_execution_stock_for_committed_bar",
            "enqueue_routine_signal",
            "consume_pending_routine_signals",
            "SendOrder",
        ):
            self.assertNotIn(forbidden, source)

        self.owner.review_mutation = Mock()
        self.owner.exclusion_mutation = Mock()
        self.owner.participant_mutation = Mock()
        repository = Mock()
        repository.resolve_stock_dir.return_value = Path("C:/temp/005930_Test")
        mismatched = _canonical()
        mismatched[0]["close"] = 999
        with patch("gui_market_data_host.StockRepository", return_value=repository), patch(
            "gui_market_data_host.load_candles", return_value=mismatched
        ), patch("gui_market_data_host.canonical_candle_content_hash", return_value="hash"):
            result = self.market._compare_or_pend_realtime_shadow_bar(
                RealtimeShadowBar(**_shadow_payload())
            )
        self.assertEqual("MISMATCH", result["status"])
        self.owner.review_mutation.assert_not_called()
        self.owner.exclusion_mutation.assert_not_called()
        self.owner.participant_mutation.assert_not_called()


class RealtimeSyncFailureIsolationTests(unittest.TestCase):
    def test_sync_failure_does_not_block_existing_tr_refresh(self) -> None:
        class Window:
            _last_time_policy_minute_key = ""

            @staticmethod
            def startup_recovery_session_ready(refresh=True):
                return True

            @staticmethod
            def recalculate_all_status_by_operation_policy(*_args, **_kwargs):
                return {"changed": 0, "failed": 0}

            @staticmethod
            def statusBarMessage(*_args, **_kwargs):
                pass

        window = Window()
        refresh = Mock(return_value={"accepted": True, "completed": True})
        market_data = SimpleNamespace(
            sync_targets=Mock(side_effect=RuntimeError("shadow sync failed")),
            prepare_operation_cycle=Mock(return_value={}),
            refresh_operation_candles=refresh,
        )
        window.market_data_host = lambda: market_data
        snapshot = SimpleNamespace(entries=(), execution_stock_codes=())
        with patch.object(gui_auto_trade_timer, "auto_trade_current_time_policy_minute_key", return_value="2026-08-20 10:15"), patch.object(
            gui_auto_trade_timer, "project_execution_universe", return_value=snapshot
        ), patch.object(gui_auto_trade_timer, "auto_trade_continue_pending_close_liquidations", return_value={"processed": 0, "blocked": 0}), patch.object(
            gui_auto_trade_timer, "auto_trade_continue_pending_manual_ats_liquidations", return_value={"processed": 0, "failed": 0}
        ), patch.object(gui_auto_trade_timer, "_process_pending_signal_pipeline", return_value={}
        ), patch.object(gui_auto_trade_timer, "observe_production_exception") as observe:
            result = gui_auto_trade_timer.auto_trade_run_operation_cycle(window)

        refresh.assert_called_once()
        observe.assert_called()
        self.assertTrue(result["processed"])
        self.assertEqual("REALTIME_SHADOW_SYNC_FAILED", result["realtime_shadow_result"]["reason_code"])


if __name__ == "__main__":
    unittest.main()
