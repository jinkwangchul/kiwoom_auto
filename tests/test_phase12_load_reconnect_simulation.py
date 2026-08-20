from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from PyQt5.QtCore import QCoreApplication, QObject

import auto_candle_refresh
from execution_universe import project_execution_universe
from gui_market_data_host import MarketDataHost
from kiwoom_api import KiwoomApi
from kiwoom_market_data_authority import (
    MarketDataAuthority,
    REALTIME_AUTHORITY,
    REALTIME_ELIGIBLE,
    REALTIME_PRIMARY,
    TR_PRIMARY_SHADOWING,
    TR_RECONCILIATION_AUTHORITY,
)
from kiwoom_realtime_shadow import RealtimeShadowBarBuilder
from kiwoom_screen_allocator import KiwoomScreenAllocator


class _Signal:
    def __init__(self) -> None:
        self.callbacks: list[object] = []

    def connect(self, callback) -> None:
        self.callbacks.append(callback)

    def emit(self, payload) -> None:
        for callback in tuple(self.callbacks):
            callback(payload)


class _Control:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def dynamicCall(self, signature, *args):
        self.calls.append((signature, args))
        return 0


def _realtime_api() -> KiwoomApi:
    api = KiwoomApi.__new__(KiwoomApi)
    api._control = _Control()
    api._available = True
    api._connected = True
    api._login_requested = False
    api._login_session_id = "SESSION-1"
    api._connection_epoch = 1
    api.last_login_error = 0
    api.last_login_message = "connected"
    api._unavailable_reason = ""
    api._screen_allocator = KiwoomScreenAllocator()
    api._realtime_shadow_builder = RealtimeShadowBarBuilder()
    api._realtime_shadow_registration = api._empty_realtime_shadow_snapshot()
    api.realtime_shadow_tick_received = _Signal()
    api.realtime_shadow_bar_completed = _Signal()
    api.login_state_changed = _Signal()
    return api


class _MarketApi:
    def __init__(self) -> None:
        self.bar_committed = _Signal()
        self.realtime_shadow_bar_completed = _Signal()
        self.snapshot = SimpleNamespace(
            active=True,
            connection_epoch=1,
            login_session_id="SESSION-1",
            target_stock_codes=("005930",),
        )

    def sync_realtime_shadow_registration(self, target_codes):
        self.snapshot = SimpleNamespace(
            active=True,
            connection_epoch=self.snapshot.connection_epoch,
            login_session_id=self.snapshot.login_session_id,
            target_stock_codes=tuple(target_codes),
        )
        return {"ok": True, "active": True, "snapshot": self.snapshot}

    def realtime_shadow_registration_snapshot(self):
        return self.snapshot

    def clear_realtime_shadow_registration(self, **_kwargs):
        return {"ok": True, "active": False}


class Phase12LoadReconnectSimulationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    @staticmethod
    def _stock(root: Path, index: int, **state_overrides: object) -> Path:
        stock_dir = root / f"{index + 1:06d}_Stock{index}"
        stock_dir.mkdir()
        state = {
            "status": "RUNNING",
            "trade_enabled": True,
            "trade_started_at": "2026-08-20 09:00:00",
        }
        state.update(state_overrides)
        (stock_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (stock_dir / "config.json").write_text("{}", encoding="utf-8")
        return stock_dir

    def test_process_restart_does_not_restore_persisted_participants(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            running = self._stock(root, 0)
            waiting = self._stock(root, 1, status="WAITING")
            excluded = self._stock(root, 2)
            (excluded / "config.json").write_text(
                json.dumps({"operation_excluded": True}), encoding="utf-8"
            )
            fresh_process = SimpleNamespace(
                startup_recovery_session_ready=Mock(return_value=True)
            )
            snapshot = project_execution_universe(
                fresh_process,
                stock_dirs=[running, waiting, excluded],
            )

        self.assertEqual((), snapshot.participant_stock_codes)
        self.assertEqual((), snapshot.execution_stock_codes)
        self.assertTrue(snapshot.global_ready)
        self.assertTrue(all(entry.execution_ready is False for entry in snapshot.entries))

    def test_tr_load_is_bounded_and_round_robin_has_no_starvation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock_dirs = [self._stock(root, index) for index in range(45)]
            requested: list[str] = []

            class Api:
                is_available = staticmethod(lambda: True)
                is_connected = staticmethod(lambda: True)

                @staticmethod
                def request_minute_candles(code, _name, **kwargs):
                    requested.append(code)
                    kwargs["callback"]({"ok": True, "rows_count": 1})
                    return {"ok": True}

            window = SimpleNamespace(kiwoom_api=Api())
            cycle_counts: list[int] = []
            with patch.object(
                auto_candle_refresh, "all_registered_stock_dirs", return_value=stock_dirs
            ), patch.object(
                auto_candle_refresh.QTimer,
                "singleShot",
                side_effect=lambda _delay, callback: callback(),
            ):
                for minute in range(3):
                    before = len(requested)
                    result = auto_candle_refresh.refresh_operation_candles(
                        window, f"2026-08-20 10:{minute:02d}"
                    )
                    cycle_counts.append(len(requested) - before)
                    self.assertEqual(30, result["skipped_by_limit"])

        self.assertEqual([15, 15, 15], cycle_counts)
        self.assertEqual(45, len(set(requested)))

    def test_tr_target_selection_boundaries_0_1_15_and_16(self) -> None:
        for count in (0, 1, 15, 16):
            with self.subTest(count=count):
                targets = [
                    (Path(f"C:/fixture/{index:06d}_Stock"), f"{index:06d}", "Stock")
                    for index in range(count)
                ]
                window = SimpleNamespace()
                selected, skipped = auto_candle_refresh._rotated_targets(window, targets)
                self.assertEqual(min(count, 15), len(selected))
                self.assertEqual(max(count - 15, 0), skipped)
                if count == 16:
                    next_selected, _ = auto_candle_refresh._rotated_targets(window, targets)
                    self.assertIn(targets[15], next_selected)

    def test_realtime_registration_batches_1_100_101_and_205(self) -> None:
        for count, expected_batches in ((1, 1), (100, 1), (101, 2), (205, 3)):
            with self.subTest(count=count):
                api = _realtime_api()
                codes = [f"{index:06d}" for index in range(1, count + 1)]
                result = api.sync_realtime_shadow_registration(codes)
                calls = [
                    call for call in api._control.calls if call[0].startswith("SetRealReg")
                ]
                sizes = [len(call[1][1].split(";")) for call in calls]
                self.assertTrue(result["active"])
                self.assertEqual(expected_batches, len(calls))
                self.assertTrue(all(size <= 100 for size in sizes))
                self.assertTrue(all(call[1][0].startswith("4") for call in calls))

    def test_new_broker_session_resets_realtime_primary_and_pending_state(self) -> None:
        owner = QObject()
        api = _MarketApi()
        market = MarketDataHost(owner, api, lambda _code: None)
        snapshot = SimpleNamespace(execution_stock_codes=("005930",))
        market.sync_targets(snapshot)
        authority = market._market_data_authority
        authority.observe_comparison(
            "005930",
            "2026-08-20 10:15",
            status="MATCH",
            price_match=True,
            volume_compared=True,
            volume_match=True,
            eligible_context=True,
        )
        authority.promote_at_cycle_boundary(
            "005930",
            readiness_valid=True,
            unresolved_pending=False,
            refresh_inflight=False,
        )
        market._operation_candle_requests["OLD_RQ"] = {"stock_code": "005930"}
        market._pending_reconciliations.add(("005930", "2026-08-20 10:16"))
        self.assertEqual(REALTIME_PRIMARY, authority.mode("005930"))

        api.snapshot = SimpleNamespace(
            active=True,
            connection_epoch=2,
            login_session_id="SESSION-2",
            target_stock_codes=("005930",),
        )
        market.sync_targets(snapshot)

        self.assertEqual(TR_PRIMARY_SHADOWING, authority.mode("005930"))
        self.assertEqual((2, "SESSION-2"), authority.session_identity)
        self.assertEqual({}, market._operation_candle_requests)
        self.assertEqual(set(), market._pending_reconciliations)
        market.shutdown()

    def test_authority_races_are_single_owner_idempotent_and_bounded(self) -> None:
        authority = MarketDataAuthority()
        authority.ensure_session(7, "SESSION-7")
        authority.sync_targets(("005930",))
        minute = "2026-08-20 10:15"
        self.assertTrue(authority.claim_authority("005930", minute, REALTIME_AUTHORITY))
        self.assertFalse(
            authority.claim_authority("005930", minute, TR_RECONCILIATION_AUTHORITY)
        )
        self.assertTrue(authority.claim_authority("005930", minute, REALTIME_AUTHORITY))
        for index in range(authority.MAX_AUTHORITY_MINUTES + 100):
            authority.claim_authority(
                "005930", f"2026-08-{index // 1440 + 1:02d} {index // 60 % 24:02d}:{index % 60:02d}", REALTIME_AUTHORITY
            )
        self.assertLessEqual(
            len(authority._minute_authority), authority.MAX_AUTHORITY_MINUTES
        )

    def test_shutdown_clears_pending_work_and_ignores_late_events(self) -> None:
        owner = QObject()
        api = _MarketApi()
        market = MarketDataHost(owner, api, lambda _code: None)
        ready: list[object] = []
        market.canonical_bar_ready_for_operation.connect(ready.append)
        market._operation_candle_requests["RQ"] = {"stock_code": "005930"}
        market._canonical_event_queue.append({"event_type": "BAR_COMMITTED"})
        market._pending_reconciliations.add(("005930", "2026-08-20 10:15"))

        market.shutdown()
        api.bar_committed.emit(
            {
                "event_type": "BAR_COMMITTED",
                "source": "opt10080",
                "timeframe_minutes": 1,
            }
        )
        market._drain_canonical_events()

        self.assertEqual([], ready)
        self.assertEqual({}, market._operation_candle_requests)
        self.assertEqual(0, len(market._canonical_event_queue))
        self.assertEqual(set(), market._pending_reconciliations)


if __name__ == "__main__":
    unittest.main()
