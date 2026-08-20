from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from PyQt5.QtCore import QCoreApplication, QObject

import auto_candle_refresh
from candle_manager import load_candles
from gui_auto_trade_operation_host import AutoTradeOperationHost
import kiwoom_candle_adapter
import kiwoom_api
from kiwoom_market_data_authority import (
    MarketDataAuthority,
    REALTIME_AUTHORITY,
    REALTIME_ELIGIBLE,
    REALTIME_PRIMARY,
    REALTIME_PRIMARY_SKIP,
    REALTIME_RECONCILIATION,
    TR_PRIMARY_SHADOWING,
    TR_RECONCILIATION_AUTHORITY,
    TR_RECONCILING,
)
from kiwoom_realtime_shadow import RealtimeShadowBar


class _Signal:
    def __init__(self) -> None:
        self.callbacks = []

    def connect(self, callback) -> None:
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
        self.commit_realtime_primary_bar = Mock(return_value={
            "ok": True,
            "changed": True,
            "commit_verified": True,
        })

    def realtime_shadow_registration_snapshot(self):
        return self.snapshot

    def clear_realtime_shadow_registration(self, **_kwargs):
        return {"ok": True, "active": False}


class _Owner(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.kiwoom_api = _Api()


def _bar(**changes) -> RealtimeShadowBar:
    payload = {
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
    payload.update(changes)
    return RealtimeShadowBar(**payload)


class MarketDataAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = MarketDataAuthority()
        self.state.ensure_session(7, "SESSION-7")
        self.state.sync_targets(("005930",))

    def test_full_match_is_eligible_then_promotes_only_at_cycle_boundary(self) -> None:
        initial = self.state.snapshot("005930")
        self.assertEqual(TR_PRIMARY_SHADOWING, initial.mode)
        eligible = self.state.observe_comparison(
            "005930",
            "2026-08-20 10:15",
            status="MATCH",
            price_match=True,
            volume_compared=True,
            volume_match=True,
            eligible_context=True,
        )
        self.assertEqual(REALTIME_ELIGIBLE, eligible.mode)
        deferred = self.state.promote_at_cycle_boundary(
            "005930", readiness_valid=True, unresolved_pending=False, refresh_inflight=True
        )
        self.assertEqual(REALTIME_ELIGIBLE, deferred.mode)
        promoted = self.state.promote_at_cycle_boundary(
            "005930", readiness_valid=True, unresolved_pending=False, refresh_inflight=False
        )
        self.assertEqual(REALTIME_PRIMARY, promoted.mode)

    def test_partial_mismatch_and_session_change_remove_eligibility(self) -> None:
        for status in ("PARTIAL_VOLUME_UNVERIFIED", "MISMATCH"):
            self.state.observe_comparison(
                "005930", "2026-08-20 10:15", status="MATCH",
                price_match=True, volume_compared=True, volume_match=True,
                eligible_context=True,
            )
            result = self.state.observe_comparison(
                "005930", "2026-08-20 10:16", status=status,
                price_match=status != "MISMATCH", volume_compared=False,
                volume_match=None, eligible_context=True,
            )
            self.assertEqual(TR_PRIMARY_SHADOWING, result.mode)
        self.state.ensure_session(8, "SESSION-8")
        reset = self.state.snapshot("005930")
        self.assertEqual(TR_PRIMARY_SHADOWING, reset.mode)
        self.assertEqual("", reset.last_full_match_minute)

    def test_per_minute_authority_is_single_owner_and_bounded(self) -> None:
        minute = "2026-08-20 10:15"
        self.assertTrue(self.state.claim_authority("005930", minute, REALTIME_AUTHORITY))
        self.assertFalse(self.state.claim_authority("005930", minute, TR_RECONCILIATION_AUTHORITY))
        self.state.mark_realtime_committed("005930", minute)
        self.assertTrue(self.state.realtime_committed("005930", minute))

    def test_expected_market_minute_has_no_premarket_synthetic_value(self) -> None:
        self.assertEqual("", self.state.expected_completed_minute("2026-08-20 09:00"))
        self.assertEqual("2026-08-20 09:00", self.state.expected_completed_minute("2026-08-20 09:01"))


class RealtimeCanonicalCommitTests(unittest.TestCase):
    def test_realtime_bar_keeps_raw_shape_and_source_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_Test"
            repository = type(
                "Repository",
                (),
                {"resolve_stock_dir": lambda _self, _code, _name="": stock_dir},
            )()
            with patch.object(kiwoom_candle_adapter, "StockRepository", return_value=repository):
                first = kiwoom_candle_adapter.commit_realtime_primary_bar_for_stock(
                    "005930", "Test", _bar()
                )
                duplicate = kiwoom_candle_adapter.commit_realtime_primary_bar_for_stock(
                    "005930", "Test", _bar()
                )
            self.assertTrue(first.ok)
            self.assertTrue(first.changed)
            self.assertFalse(duplicate.changed)
            self.assertEqual("realtime_primary", first.notification.source)
            self.assertEqual("SESSION-7", first.notification.login_session_id)
            self.assertEqual(
                {"time", "open", "high", "low", "close", "volume"},
                set(load_candles(stock_dir)[0]),
            )

    def test_incomplete_realtime_bar_is_rejected_by_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_Test"
            repository = type(
                "Repository",
                (),
                {"resolve_stock_dir": lambda _self, _code, _name="": stock_dir},
            )()
            with patch.object(kiwoom_candle_adapter, "StockRepository", return_value=repository):
                result = kiwoom_candle_adapter.commit_realtime_primary_bar_for_stock(
                    "005930", "Test", _bar(volume=None, volume_complete=False)
                )
            self.assertFalse(result.ok)
            self.assertEqual("INVALID_REALTIME_BAR", result.error_kind)

    def test_central_api_publishes_verified_changed_commit_exactly_once(self) -> None:
        signal = _Signal()
        emitted = []
        signal.connect(emitted.append)
        fake = SimpleNamespace(
            _ensure_realtime_shadow_state=lambda: None,
            _realtime_shadow_registration=SimpleNamespace(
                active=True,
                connection_epoch=7,
                login_session_id="SESSION-7",
                target_stock_codes=("005930",),
            ),
            broker_session_snapshot=lambda: SimpleNamespace(
                connected=True,
                connection_epoch=7,
                login_session_id="SESSION-7",
            ),
            bar_committed=signal,
        )
        notification = SimpleNamespace(to_payload=lambda: {"source": "realtime_primary"})
        commit = SimpleNamespace(
            ok=True, changed=True, readback_verified=True,
            canonical_content_hash="hash", path="candles.json",
            commit_identity="commit", bar_key="key", bar_identity="bar",
            bar_time="2026-08-20T10:15:00+09:00", trade_date="2026-08-20",
            error_kind="", error="", notification=notification,
        )
        with patch.object(kiwoom_api, "commit_realtime_primary_bar_for_stock", return_value=commit):
            result = kiwoom_api.KiwoomApi.commit_realtime_primary_bar(fake, _bar(), stock_name="Test")
        self.assertTrue(result["commit_verified"])
        self.assertEqual([{"source": "realtime_primary"}], emitted)

    def test_central_api_rejects_wrong_session_without_publish(self) -> None:
        signal = _Signal()
        emitted = []
        signal.connect(emitted.append)
        fake = SimpleNamespace(
            _ensure_realtime_shadow_state=lambda: None,
            _realtime_shadow_registration=SimpleNamespace(
                active=True, connection_epoch=8, login_session_id="SESSION-8",
                target_stock_codes=("005930",),
            ),
            broker_session_snapshot=lambda: SimpleNamespace(
                connected=True, connection_epoch=8, login_session_id="SESSION-8",
            ),
            bar_committed=signal,
        )
        result = kiwoom_api.KiwoomApi.commit_realtime_primary_bar(fake, _bar())
        self.assertFalse(result["ok"])
        self.assertEqual([], emitted)


class ReconciliationRequestEntryPointTests(unittest.TestCase):
    def test_reconciliation_reuses_request_minute_candles_and_owned_context(self) -> None:
        terminal = []
        registered = []

        class Window:
            def __init__(self) -> None:
                self.kiwoom_api = SimpleNamespace(
                    request_minute_candles=Mock(return_value={"ok": True, "rqname": "rq-reconcile"})
                )

            def register_operation_candle_request(self, rqname, **kwargs):
                registered.append((rqname, kwargs))

        window = Window()
        result = auto_candle_refresh.request_operation_candle_for_stock(
            window,
            Path("C:/temp/005930_Test"),
            "005930",
            "Test",
            operation_cycle_minute_key="2026-08-20 10:16",
            request_kind="REALTIME_RECONCILIATION",
            reconciliation_minute="2026-08-20 10:15",
            on_terminal=terminal.append,
        )
        self.assertTrue(result["ok"])
        window.kiwoom_api.request_minute_candles.assert_called_once()
        self.assertEqual("REALTIME_RECONCILIATION", registered[0][1]["request_kind"])
        self.assertEqual("2026-08-20 10:15", registered[0][1]["reconciliation_minute"])
        self.assertEqual([], terminal)


class RealtimePrimaryOperationHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self) -> None:
        self.owner = _Owner()
        self.host = AutoTradeOperationHost(self.owner)
        self.host._realtime_shadow_session_identity = (7, "SESSION-7")
        state = self.host._market_data_authority
        state.ensure_session(7, "SESSION-7")
        state.sync_targets(("005930",))
        state.observe_comparison(
            "005930", "2026-08-20 10:14", status="MATCH",
            price_match=True, volume_compared=True, volume_match=True,
            eligible_context=True,
        )
        state.promote_at_cycle_boundary(
            "005930", readiness_valid=True, unresolved_pending=False, refresh_inflight=False
        )

    def tearDown(self) -> None:
        self.host.shutdown()

    @staticmethod
    def _ready_snapshot(ready: bool = True):
        return SimpleNamespace(entries=(SimpleNamespace(
            execution_ready=ready,
            stock_name="Test",
        ),))

    def test_valid_primary_bar_commits_once_and_later_cycle_skips_tr(self) -> None:
        with patch("gui_auto_trade_operation_host.project_execution_universe", return_value=self._ready_snapshot()), patch(
            "gui_auto_trade_operation_host.StockRepository"
        ):
            first = self.host._process_realtime_primary_bar(_bar())
            duplicate = self.host._process_realtime_primary_bar(_bar())
        self.assertTrue(first["ok"])
        self.assertEqual("REALTIME_BAR_ALREADY_COMMITTED", duplicate["reason_code"])
        self.owner.kiwoom_api.commit_realtime_primary_bar.assert_called_once()
        decision = self.host.market_data_refresh_decision("005930", "2026-08-20 10:16")
        self.assertEqual(REALTIME_PRIMARY_SKIP, decision["decision"])

    def test_tr_claim_first_blocks_late_realtime_writer(self) -> None:
        decision = self.host.market_data_refresh_decision("005930", "2026-08-20 10:16")
        self.assertEqual(REALTIME_RECONCILIATION, decision["decision"])
        with patch("gui_auto_trade_operation_host.project_execution_universe", return_value=self._ready_snapshot()), patch(
            "gui_auto_trade_operation_host.StockRepository"
        ):
            result = self.host._process_realtime_primary_bar(_bar())
        self.assertEqual("TR_RECONCILIATION_OWNS_MINUTE", result["reason_code"])
        self.owner.kiwoom_api.commit_realtime_primary_bar.assert_not_called()

    def test_older_completed_event_cannot_rewrite_canonical(self) -> None:
        state = self.host._market_data_authority
        state.claim_authority("005930", "2026-08-20 10:16", REALTIME_AUTHORITY)
        state.mark_realtime_committed("005930", "2026-08-20 10:16")
        result = self.host._process_realtime_primary_bar(_bar())
        self.assertEqual("STALE_REALTIME_BAR", result["reason_code"])
        self.owner.kiwoom_api.commit_realtime_primary_bar.assert_not_called()

    def test_incomplete_volume_and_commit_failure_fall_back_once(self) -> None:
        request = Mock(return_value=True)
        with patch.object(self.host, "_request_realtime_reconciliation", request), patch(
            "gui_auto_trade_operation_host.project_execution_universe", return_value=self._ready_snapshot()
        ), patch("gui_auto_trade_operation_host.StockRepository"):
            incomplete = self.host._process_realtime_primary_bar(
                _bar(volume=None, volume_complete=False)
            )
        self.assertFalse(incomplete["ok"])
        self.assertEqual(TR_RECONCILING, self.host.market_data_mode_snapshot("005930").mode)
        request.assert_called_once_with("005930", "2026-08-20 10:15")

        self.host._pending_reconciliations.clear()
        state = self.host._market_data_authority
        state.finish_reconciliation("005930", "2026-08-20 10:15", repaired=True)
        state.observe_comparison(
            "005930", "2026-08-20 10:16", status="MATCH",
            price_match=True, volume_compared=True, volume_match=True,
            eligible_context=True,
        )
        state.promote_at_cycle_boundary(
            "005930", readiness_valid=True, unresolved_pending=False, refresh_inflight=False
        )
        self.owner.kiwoom_api.commit_realtime_primary_bar.return_value = {
            "ok": False,
            "changed": False,
            "commit_verified": False,
        }
        request.reset_mock()
        with patch.object(self.host, "_request_realtime_reconciliation", request), patch(
            "gui_auto_trade_operation_host.project_execution_universe", return_value=self._ready_snapshot()
        ), patch("gui_auto_trade_operation_host.StockRepository"):
            failed = self.host._process_realtime_primary_bar(
                _bar(bar_time="2026-08-20T10:16:00+09:00")
            )
        self.assertEqual("REALTIME_CANONICAL_COMMIT_FAILED", failed["reason_code"])
        request.assert_called_once_with("005930", "2026-08-20 10:16")

    def test_fake_realtime_event_is_not_a_fast_path_trigger(self) -> None:
        event = {
            "event_type": "BAR_COMMITTED",
            "source": "realtime_primary",
            "stock_code": "006400",
            "timeframe_minutes": 1,
            "bar_time": "2026-08-20T10:15:00+09:00",
            "bar_key": "key",
            "bar_identity": "bar",
            "commit_identity": "commit",
            "canonical_content_hash": "hash",
            "canonical_path": "C:/temp/candles.json",
            "login_session_id": "SESSION-7",
            "connection_epoch": 7,
        }
        self.host._on_bar_committed(event)
        self.assertEqual(0, len(self.host._bar_commit_trigger_queue))

    def test_realtime_context_uses_next_minute_evaluation_key(self) -> None:
        state = self.host._market_data_authority
        state.claim_authority("005930", "2026-08-20 10:15", REALTIME_AUTHORITY)
        event = {
            "stock_code": "005930",
            "stock_name": "Test",
            "bar_time": "2026-08-20T10:15:00+09:00",
            "connection_epoch": 7,
            "login_session_id": "SESSION-7",
        }
        with patch("gui_auto_trade_operation_host.StockRepository"):
            context = self.host._realtime_bar_commit_context(event)
        self.assertEqual("2026-08-20 10:16", context["operation_cycle_minute_key"])

    def test_reconciliation_requires_target_minute_in_canonical(self) -> None:
        state = self.host._market_data_authority
        state.begin_reconciliation("005930", "2026-08-20 10:15", "TEST")
        self.host._pending_reconciliations.add(("005930", "2026-08-20 10:15"))
        with patch("gui_auto_trade_operation_host.load_candles", return_value=[]):
            repaired = self.host.complete_realtime_reconciliation(
                "005930", "2026-08-20 10:15", Path("C:/temp"), {"ok": True}
            )
        self.assertFalse(repaired)
        self.assertEqual(TR_RECONCILING, self.host.market_data_mode_snapshot("005930").mode)
        with patch("gui_auto_trade_operation_host.load_candles", return_value=[{
            "time": "20260820101500", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1,
        }]):
            repaired = self.host.complete_realtime_reconciliation(
                "005930", "2026-08-20 10:15", Path("C:/temp"), {"ok": True}
            )
        self.assertTrue(repaired)
        self.assertEqual(TR_PRIMARY_SHADOWING, self.host.market_data_mode_snapshot("005930").mode)

    def test_post_reconciliation_comparison_is_not_promotion_evidence(self) -> None:
        state = self.host._market_data_authority
        state.force_tr_primary("005930", "TEST")
        state.replace_with_reconciliation_authority("005930", "2026-08-20 10:15")
        canonical = [{
            "time": "20260820101500", "open": 100, "high": 105,
            "low": 98, "close": 103, "volume": 40,
        }]
        with patch("gui_auto_trade_operation_host.StockRepository"), patch(
            "gui_auto_trade_operation_host.load_candles", return_value=canonical
        ), patch(
            "gui_auto_trade_operation_host.canonical_candle_content_hash", return_value="hash"
        ), patch(
            "gui_auto_trade_operation_host.project_execution_universe", return_value=self._ready_snapshot()
        ):
            result = self.host._compare_or_pend_realtime_shadow_bar(_bar())
        self.assertEqual("MATCH", result["status"])
        self.assertEqual(TR_PRIMARY_SHADOWING, self.host.market_data_mode_snapshot("005930").mode)


if __name__ == "__main__":
    unittest.main()
