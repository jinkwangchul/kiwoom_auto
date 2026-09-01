from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PyQt5.QtCore import QCoreApplication, QObject

import gui_auto_trade_timer
import gui_main_table_loader as main_loader
from budget_command import inspect_budget_value_entry
from candle_timeframe_aggregation import SEOUL_TIMEZONE
from gui_auto_trade_operation_host import AutoTradeOperationHost
from gui_ats_utils import project_manual_ats_execution_order
from gui_market_data_host import (
    HighResolutionMarketState,
    InitialMarketSnapshotState,
    MarketDataHost,
)
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
        self.connected = True
        self.connection_epoch = 7
        self.login_session_id = "SESSION-7"

    def realtime_shadow_registration_snapshot(self):
        return self.snapshot

    def broker_session_snapshot(self):
        return SimpleNamespace(
            connected=self.connected,
            connection_epoch=self.connection_epoch,
            login_session_id=self.login_session_id,
        )


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


class ActionableRealtimePriceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self) -> None:
        self.owner = _Owner()
        self.host = AutoTradeOperationHost(self.owner)
        self.market = self.host.market_data_host()
        self.market._realtime_shadow_session_identity = (7, "SESSION-7")

    def tearDown(self) -> None:
        self.host.shutdown()

    @staticmethod
    def _now(day: int, hour: int = 10) -> datetime:
        return datetime(2026, 8, day, hour, 0, tzinfo=SEOUL_TIMEZONE)

    def _snapshot(self, price: int = 72_000) -> InitialMarketSnapshotState:
        return InitialMarketSnapshotState(
            stock_code="005930",
            connection_epoch=7,
            login_session_id="SESSION-7",
            current_price=price,
            open_price=price - 1_000,
            high_price=price + 1_000,
            low_price=price - 2_000,
            change_rate=1.0,
            previous_day_volume_rate=2.0,
            execution_strength=100.0,
            cumulative_volume=1_000,
            received_at="2026-08-30T09:00:00+09:00",
        )

    def _realtime(
        self,
        *,
        day: int,
        price: int = 70_000,
        epoch: int = 7,
        session_id: str = "SESSION-7",
    ) -> HighResolutionMarketState:
        observed = self._now(day).isoformat()
        return HighResolutionMarketState(
            stock_code="005930",
            connection_epoch=epoch,
            login_session_id=session_id,
            last_execution_time_raw="100000",
            last_market_datetime=observed,
            last_price=price,
            last_trade_volume_raw=1,
            last_trade_volume_abs=1,
            last_cumulative_volume=1_000,
            last_receive_sequence=1,
            last_received_at=observed,
            last_received_monotonic=1.0,
            received_tick_count=1,
            processed_tick_count=1,
            data_quality="NORMAL",
        )

    def test_snapshot_is_display_only_and_budget_waits_for_first_tick(self) -> None:
        self.market._initial_market_snapshot_states["005930"] = self._snapshot()
        display = self.market.monitoring_market_information_state("005930")
        actionable = self.market.fresh_monitoring_market_information_state(
            "005930", now_dt=self._now(30)
        )

        self.assertEqual(72_000, display.last_price)
        self.assertEqual("SNAPSHOT", dict(display.field_sources)["last_price"])
        self.assertIsNone(actionable)

        window = SimpleNamespace(
            main_monitoring_auto_trade_operation_host=lambda: self.host,
            kiwoom_api=SimpleNamespace(is_connected=lambda: True),
            selected_account_no=lambda: "12345678",
            _account_authentication_states={"12345678": "READY"},
        )
        stock = {
            "code": "005930",
            "name": "삼성전자",
            "stock_path": "",
        }
        fresh_price = self.market.fresh_monitoring_market_information_state
        with patch.object(
            self.market,
            "fresh_monitoring_market_information_state",
            side_effect=lambda code: fresh_price(code, now_dt=self._now(30)),
        ):
            display_budget = main_loader.main_stock_resolved_initial_buy_display(
                window,
                stock,
                {"trade_amount_type": "QUANTITY", "buy_qty": 2},
            )
        self.assertEqual("대기", display_budget["value_text"])

    def test_today_realtime_is_actionable_and_recalculates_budget(self) -> None:
        self.market._initial_market_snapshot_states["005930"] = self._snapshot()
        self.market._high_resolution_market_states["005930"] = self._realtime(
            day=30, price=70_000
        )
        actionable = self.market.fresh_monitoring_market_information_state(
            "005930", now_dt=self._now(30)
        )

        self.assertEqual(70_000, actionable.last_price)
        self.assertEqual("REALTIME", dict(actionable.field_sources)["last_price"])

        window = SimpleNamespace(main_monitoring_auto_trade_operation_host=lambda: self.host)
        stock = {"code": "005930", "name": "삼성전자", "stock_path": ""}
        fresh_price = self.market.fresh_monitoring_market_information_state
        with patch.object(
            self.market,
            "fresh_monitoring_market_information_state",
            side_effect=lambda code: fresh_price(code, now_dt=self._now(30)),
        ):
            amount = main_loader.main_stock_resolved_starting_budget(
                window,
                stock,
                {"trade_amount_type": "QUANTITY", "buy_qty": 2},
            )
        self.assertEqual(140_000, amount)

    def test_previous_day_realtime_is_not_actionable(self) -> None:
        self.market._high_resolution_market_states["005930"] = self._realtime(day=20)

        self.assertIsNone(
            self.market.fresh_monitoring_market_information_state(
                "005930", now_dt=self._now(30)
            )
        )
        self.assertEqual(
            70_000,
            self.market.monitoring_market_information_state("005930").last_price,
        )

    def test_overnight_rollover_is_not_actionable(self) -> None:
        self.market._high_resolution_market_states["005930"] = self._realtime(day=29)

        same_day = self.market.fresh_monitoring_market_information_state(
            "005930", now_dt=self._now(29, 23)
        )
        next_day = self.market.fresh_monitoring_market_information_state(
            "005930", now_dt=self._now(30, 0)
        )
        display = self.market.monitoring_market_information_state("005930")

        self.assertEqual(70_000, same_day.last_price)
        self.assertIsNone(next_day)
        self.assertEqual(70_000, display.last_price)

    def test_weekend_snapshot_remains_display_only(self) -> None:
        sunday = self._now(30)
        self.assertEqual(6, sunday.weekday())
        self.market._initial_market_snapshot_states["005930"] = self._snapshot()

        self.assertEqual(
            72_000,
            self.market.monitoring_market_information_state("005930").last_price,
        )
        self.assertIsNone(
            self.market.fresh_monitoring_market_information_state(
                "005930", now_dt=sunday
            )
        )

    def test_disconnect_blocks_actionable_without_clearing_display_state(self) -> None:
        self.market._high_resolution_market_states["005930"] = self._realtime(day=30)
        self.owner.kiwoom_api.connected = False

        self.assertIsNone(
            self.market.fresh_monitoring_market_information_state(
                "005930", now_dt=self._now(30)
            )
        )
        self.assertEqual(
            70_000,
            self.market.monitoring_market_information_state("005930").last_price,
        )

    def test_reconnect_keeps_last_known_display_until_new_current_tick(self) -> None:
        self.market._high_resolution_market_states["005930"] = self._realtime(day=30)
        self.market._clear_session_state()
        self.owner.kiwoom_api.connection_epoch = 8
        self.owner.kiwoom_api.login_session_id = "SESSION-8"
        self.market._realtime_shadow_session_identity = (8, "SESSION-8")

        self.assertEqual(
            70_000,
            self.market.monitoring_market_information_state("005930").last_price,
        )
        self.assertIsNone(
            self.market.fresh_monitoring_market_information_state(
                "005930", now_dt=self._now(30)
            )
        )

        self.market._high_resolution_market_states["005930"] = self._realtime(
            day=30,
            price=71_000,
            epoch=8,
            session_id="SESSION-8",
        )
        self.assertEqual(
            71_000,
            self.market.fresh_monitoring_market_information_state(
                "005930", now_dt=self._now(30)
            ).last_price,
        )

    def test_same_day_silence_remains_actionable_without_ttl(self) -> None:
        self.market._high_resolution_market_states["005930"] = self._realtime(day=30)
        morning = self.market.fresh_monitoring_market_information_state(
            "005930", now_dt=self._now(30, 10)
        )
        late = self.market.fresh_monitoring_market_information_state(
            "005930", now_dt=self._now(30, 23)
        )
        self.assertEqual(70_000, morning.last_price)
        self.assertEqual(70_000, late.last_price)

    def test_budget_command_and_ats_current_price_share_actionable_result(self) -> None:
        stock_dir = Path("stocks/005930_삼성전자")
        request = stock_dir / "config.json"
        self.market._initial_market_snapshot_states["005930"] = self._snapshot()
        window = SimpleNamespace(main_monitoring_auto_trade_operation_host=lambda: self.host)
        phase = {
            "evaluable": True,
            "mode": "CONTINUOUS",
            "phase": "ACTIVE_SESSION",
            "active": True,
            "active_sessions": ("extra1",),
            "future_session_exists": False,
            "final_session_ended": False,
            "sessions": (),
            "invalid_sessions": (),
        }
        order = {
            "code": "005930",
            "side": "BUY",
            "price": 1,
            "order_intent": {"side": "BUY", "hoga": "LIMIT"},
        }
        state = {
            "manual_ats_selection": {
                "selected_sessions": ["extra1"],
                "execution_method": "CURRENT_PRICE",
            }
        }

        fresh_price = self.market.fresh_monitoring_market_information_state
        with patch.object(
            self.market,
            "fresh_monitoring_market_information_state",
            side_effect=lambda code: fresh_price(code, now_dt=self._now(30)),
        ):
            snapshot_budget = inspect_budget_value_entry(window, request)
            snapshot_ats = project_manual_ats_execution_order(
                order,
                {"operation_mode": "CONTINUOUS"},
                state,
                current_price_getter=lambda code: getattr(
                    self.host.fresh_monitoring_market_information_state(code),
                    "last_price",
                    None,
                ),
                session_phase=phase,
            )
            self.market._high_resolution_market_states["005930"] = self._realtime(
                day=30, price=73_000
            )
            realtime_budget = inspect_budget_value_entry(window, request)
            realtime_ats = project_manual_ats_execution_order(
                order,
                {"operation_mode": "CONTINUOUS"},
                state,
                current_price_getter=lambda code: getattr(
                    self.host.fresh_monitoring_market_information_state(code),
                    "last_price",
                    None,
                ),
                session_phase=phase,
            )

        self.assertEqual("CURRENT_PRICE_UNAVAILABLE", snapshot_budget["reason"])
        self.assertEqual("ATS_CURRENT_PRICE_UNAVAILABLE", snapshot_ats["reason_code"])
        self.assertTrue(realtime_budget["allowed"])
        self.assertEqual(73_000, realtime_ats["order"]["price"])


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
