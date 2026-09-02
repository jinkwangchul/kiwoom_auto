from __future__ import annotations

import inspect
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from PyQt5.QtCore import QCoreApplication, QObject
from PyQt5.QtWidgets import QWidget

from candle_manager import commit_candles
from gui_auto_trade_operation_host import AutoTradeOperationHost
import gui_main_table_loader as main_loader
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
        self.realtime_shadow_tick_received = _Signal()
        self.snapshot = SimpleNamespace(
            active=True,
            connection_epoch=7,
            login_session_id="SESSION-7",
            target_stock_codes=("005930",),
            shadow_target_stock_codes=("005930",),
        )
        self.sync_realtime_monitoring_registration = Mock(return_value={
            "ok": True,
            "active": True,
            "snapshot": self.snapshot,
        })
        self.request_initial_market_snapshot = Mock(return_value={
            "ok": True,
            "status": "ENQUEUED",
        })
        self.sync_realtime_shadow_targets = Mock(return_value={
            "ok": True,
            "active": True,
            "snapshot": self.snapshot,
        })
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

    @staticmethod
    def broker_session_snapshot():
        return SimpleNamespace(
            connected=True,
            connection_epoch=7,
            login_session_id="SESSION-7",
        )


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
        self.assertEqual(1, self.owner.kiwoom_api.realtime_shadow_tick_received.connect_count)
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

    def test_raw_tick_callback_only_enqueues_until_scheduled_drain(self) -> None:
        self._activate_raw_session()
        scheduled = []
        with patch(
            "gui_market_data_host.QTimer.singleShot",
            side_effect=lambda _ms, callback: scheduled.append(callback),
        ):
            self.owner.kiwoom_api.realtime_shadow_tick_received.emit(
                self._raw_tick_payload(sequence=100)
            )
            self.assertIsNone(self.market.high_resolution_market_state("005930"))
            self.assertEqual(1, self.market.high_resolution_market_data_snapshot().current_queue_depth)
            self.assertEqual(1, len(scheduled))
            scheduled.pop(0)()

        state = self.market.high_resolution_market_state("005930")
        self.assertIsNotNone(state)
        self.assertEqual(100, state.last_receive_sequence)

    def test_raw_tick_fifo_order_is_preserved_without_coalescing(self) -> None:
        self._activate_raw_session()
        scheduled = []
        with patch(
            "gui_market_data_host.QTimer.singleShot",
            side_effect=lambda _ms, callback: scheduled.append(callback),
        ), patch.object(
            self.market,
            "_process_raw_realtime_tick",
            wraps=self.market._process_raw_realtime_tick,
        ) as process:
            for sequence in (100, 101, 102):
                self.owner.kiwoom_api.realtime_shadow_tick_received.emit(
                    self._raw_tick_payload(sequence=sequence, price=sequence)
                )
            self.assertEqual(1, len(scheduled))
            scheduled.pop(0)()

        self.assertEqual(
            [100, 101, 102],
            [call.args[0]["receive_sequence"] for call in process.call_args_list],
        )
        self.assertEqual(
            102,
            self.market.high_resolution_market_state("005930").last_price,
        )

    def test_raw_tick_state_is_independent_per_stock(self) -> None:
        self._activate_raw_session()
        self.market._process_raw_realtime_tick(
            self._raw_tick_payload(stock_code="005930", sequence=100, price=70000)
        )
        self.market._process_raw_realtime_tick(
            self._raw_tick_payload(stock_code="000660", sequence=101, price=210000)
        )
        self.market._process_raw_realtime_tick(
            self._raw_tick_payload(stock_code="005930", sequence=102, price=70100)
        )

        samsung = self.market.high_resolution_market_state("005930")
        hynix = self.market.high_resolution_market_state("000660")
        self.assertEqual((70100, 102), (samsung.last_price, samsung.last_receive_sequence))
        self.assertEqual((210000, 101), (hynix.last_price, hynix.last_receive_sequence))

    def test_same_session_none_preserves_previous_optional_market_fields(self) -> None:
        self._activate_raw_session()
        first = self._raw_tick_payload(sequence=100)
        first.update(
            open_price=69000,
            high_price=71000,
            low_price=68000,
            change_rate=1.25,
            previous_day_volume_rate=-12.43,
            execution_strength=117.2,
        )
        self.assertTrue(self.market._process_raw_realtime_tick(first))
        second = self._raw_tick_payload(sequence=101, price=70100)
        second.update(
            open_price=None,
            high_price=None,
            low_price=None,
            change_rate=None,
            previous_day_volume_rate=None,
            execution_strength=None,
        )
        self.assertTrue(self.market._process_raw_realtime_tick(second))

        state = self.market.high_resolution_market_state("005930")
        self.assertEqual((69000, 71000, 68000), (
            state.open_price, state.high_price, state.low_price
        ))
        self.assertEqual((1.25, -12.43, 117.2), (
            state.change_rate,
            state.previous_day_volume_rate,
            state.execution_strength,
        ))

    def test_monitoring_and_execution_target_sync_are_independent(self) -> None:
        self.market.sync_monitoring_targets(("005930", "006400"))
        snapshot_call = (
            self.owner.kiwoom_api.request_initial_market_snapshot.call_args
        )
        self.assertEqual((("005930", "006400"),), snapshot_call.args)
        self.assertTrue(callable(snapshot_call.kwargs.get("callback")))
        self.owner.kiwoom_api.sync_realtime_monitoring_registration.assert_called_once_with(
            ("005930", "006400")
        )
        self.market.sync_targets(SimpleNamespace(execution_stock_codes=("005930",)))
        self.owner.kiwoom_api.sync_realtime_shadow_targets.assert_called_once_with(
            ("005930",)
        )

    def test_snapshot_is_requested_once_per_target_per_session_before_realtime(self) -> None:
        calls: list[str] = []
        self.owner.kiwoom_api.request_initial_market_snapshot.side_effect = (
            lambda *_args, **_kwargs: calls.append("snapshot")
            or {"ok": True, "status": "ENQUEUED"}
        )
        self.owner.kiwoom_api.sync_realtime_monitoring_registration.side_effect = (
            lambda _targets: calls.append("realtime")
            or {"ok": True, "active": True, "snapshot": self.owner.kiwoom_api.snapshot}
        )

        self.market.sync_monitoring_targets(("005930",))
        self.market.sync_monitoring_targets(("005930",))

        self.assertEqual(["snapshot", "realtime", "realtime"], calls)
        self.assertEqual(
            1,
            self.owner.kiwoom_api.request_initial_market_snapshot.call_count,
        )

    def test_new_218410_valid_snapshot_enables_configuration_price(self) -> None:
        self.market.sync_monitoring_targets(("218410",))
        callback = self.owner.kiwoom_api.request_initial_market_snapshot.call_args.kwargs[
            "callback"
        ]

        callback(self._snapshot_result(stock_code="218410", current_price=45_000))

        state = self.market.configuration_market_information_state("218410")
        self.assertIsNotNone(state)
        self.assertEqual(45_000, state.last_price)
        self.assertEqual("SNAPSHOT", dict(state.field_sources)["last_price"])
        self.assertNotIn(
            "218410", self.market._initial_snapshot_requested_stock_codes
        )
        self.market.sync_monitoring_targets(("218410",))
        self.assertEqual(
            1, self.owner.kiwoom_api.request_initial_market_snapshot.call_count
        )

    def test_enqueue_failure_restores_retry_eligibility(self) -> None:
        self.owner.kiwoom_api.request_initial_market_snapshot.return_value = {
            "ok": False,
            "status": "FAILED",
        }

        self.market.sync_monitoring_targets(("218410",))

        self.assertNotIn(
            "218410", self.market._initial_snapshot_requested_stock_codes
        )
        self.owner.kiwoom_api.request_initial_market_snapshot.return_value = {
            "ok": True,
            "status": "ENQUEUED",
        }
        self.market.sync_monitoring_targets(("218410",))
        self.assertEqual(
            2, self.owner.kiwoom_api.request_initial_market_snapshot.call_count
        )

    def test_partial_batch_enqueue_failure_keeps_success_batch_inflight(self) -> None:
        self.owner.kiwoom_api.request_initial_market_snapshot.return_value = {
            "ok": False,
            "status": "ENQUEUED",
            "batches": [
                {"ok": True, "stock_codes": ["000660"]},
                {"ok": False, "stock_codes": ["218410"]},
            ],
        }

        self.market.sync_monitoring_targets(("000660", "218410"))

        self.assertIn(
            "000660", self.market._initial_snapshot_requested_stock_codes
        )
        self.assertNotIn(
            "218410", self.market._initial_snapshot_requested_stock_codes
        )

    def test_request_exception_restores_retry_eligibility(self) -> None:
        self.owner.kiwoom_api.request_initial_market_snapshot.side_effect = [
            RuntimeError("enqueue failed"),
            {"ok": True, "status": "ENQUEUED"},
        ]

        failed = self.market.sync_monitoring_targets(("218410",))

        self.assertFalse(failed["ok"])
        self.assertNotIn(
            "218410", self.market._initial_snapshot_requested_stock_codes
        )
        self.market.sync_monitoring_targets(("218410",))
        self.assertEqual(
            2, self.owner.kiwoom_api.request_initial_market_snapshot.call_count
        )

    def test_empty_snapshot_restores_retry_eligibility(self) -> None:
        self.market.sync_monitoring_targets(("218410",))
        callback = self.owner.kiwoom_api.request_initial_market_snapshot.call_args.kwargs[
            "callback"
        ]

        callback(self._snapshot_result(stock_code="218410", rows=[]))

        self.assertIsNone(
            self.market.configuration_market_information_state("218410")
        )
        self.assertNotIn(
            "218410", self.market._initial_snapshot_requested_stock_codes
        )
        self.market.sync_monitoring_targets(("218410",))
        self.assertEqual(
            2, self.owner.kiwoom_api.request_initial_market_snapshot.call_count
        )

    def test_tooltip_does_not_claim_price_receipt_without_valid_evidence(self) -> None:
        tooltip = main_loader.main_stock_row_tooltip_from_projection(
            {
                "market": "KOSDAQ",
                "stock_code": "218410",
                "stock_name": "RFHIC",
                "current_price": None,
            }
        )

        self.assertIn("현재가 -", tooltip)
        self.assertNotIn("주가 수신 완료", tooltip)

    def test_timeout_snapshot_restores_retry_eligibility(self) -> None:
        self.market.sync_monitoring_targets(("218410",))
        callback = self.owner.kiwoom_api.request_initial_market_snapshot.call_args.kwargs[
            "callback"
        ]

        callback(
            {
                "ok": False,
                "type": "initial_market_snapshot",
                "stock_codes": ["218410"],
                "error_kind": "TIMEOUT",
            }
        )

        self.assertNotIn(
            "218410", self.market._initial_snapshot_requested_stock_codes
        )
        self.market.sync_monitoring_targets(("218410",))
        self.assertEqual(
            2, self.owner.kiwoom_api.request_initial_market_snapshot.call_count
        )

    def test_malformed_or_zero_snapshot_restores_retry_eligibility(self) -> None:
        for bad_price in (0, "not-a-price"):
            with self.subTest(price=bad_price):
                owner = _Owner()
                host = AutoTradeOperationHost(owner)
                market = host.market_data_host()
                self.addCleanup(host.shutdown)
                market.sync_monitoring_targets(("218410",))
                callback = owner.kiwoom_api.request_initial_market_snapshot.call_args.kwargs[
                    "callback"
                ]

                callback(
                    self._snapshot_result(
                        stock_code="218410",
                        current_price=bad_price,
                    )
                )

                self.assertIsNone(
                    market.configuration_market_information_state("218410")
                )
                self.assertNotIn(
                    "218410", market._initial_snapshot_requested_stock_codes
                )
                market.sync_monitoring_targets(("218410",))
                self.assertEqual(
                    2, owner.kiwoom_api.request_initial_market_snapshot.call_count
                )

    def test_failed_snapshot_retries_once_then_success_is_stable(self) -> None:
        self.market.sync_monitoring_targets(("218410",))
        first_callback = (
            self.owner.kiwoom_api.request_initial_market_snapshot.call_args.kwargs[
                "callback"
            ]
        )
        first_callback(self._snapshot_result(stock_code="218410", rows=[]))
        self.market.sync_monitoring_targets(("218410",))
        second_callback = (
            self.owner.kiwoom_api.request_initial_market_snapshot.call_args.kwargs[
                "callback"
            ]
        )

        second_callback(
            self._snapshot_result(stock_code="218410", current_price=45_000)
        )
        for _ in range(3):
            self.market.sync_monitoring_targets(("218410",))

        state = self.market.configuration_market_information_state("218410")
        self.assertEqual(45_000, state.last_price)
        self.assertEqual(
            2, self.owner.kiwoom_api.request_initial_market_snapshot.call_count
        )

    def test_snapshot_retry_is_bounded_without_polling(self) -> None:
        self.market.sync_monitoring_targets(("218410",))
        for expected_call_count in (1, 2):
            callback = (
                self.owner.kiwoom_api.request_initial_market_snapshot.call_args.kwargs[
                    "callback"
                ]
            )
            callback(self._snapshot_result(stock_code="218410", rows=[]))
            self.market.sync_monitoring_targets(("218410",))
            self.assertEqual(
                min(expected_call_count + 1, 2),
                self.owner.kiwoom_api.request_initial_market_snapshot.call_count,
            )
        self.assertEqual(
            2,
            self.market._initial_snapshot_request_attempts_by_stock["218410"],
        )

    def test_stale_callback_cannot_release_new_session_inflight_marker(self) -> None:
        self.market.sync_monitoring_targets(("218410",))
        old_callback = (
            self.owner.kiwoom_api.request_initial_market_snapshot.call_args.kwargs[
                "callback"
            ]
        )
        self.owner.kiwoom_api.snapshot.connection_epoch = 8
        self.owner.kiwoom_api.snapshot.login_session_id = "SESSION-8"
        self.owner.kiwoom_api.broker_session_snapshot = Mock(
            return_value=SimpleNamespace(
                connected=True,
                connection_epoch=8,
                login_session_id="SESSION-8",
            )
        )
        self.market.sync_monitoring_targets(("218410",))
        new_callback = (
            self.owner.kiwoom_api.request_initial_market_snapshot.call_args.kwargs[
                "callback"
            ]
        )

        old_callback(self._snapshot_result(stock_code="218410", current_price=1))

        self.assertIn(
            "218410", self.market._initial_snapshot_requested_stock_codes
        )
        self.assertIsNone(self.market.initial_market_snapshot_state("218410"))
        new_callback(
            self._snapshot_result(
                stock_code="218410",
                current_price=45_000,
                epoch=8,
                session_id="SESSION-8",
            )
        )
        self.assertEqual(
            45_000,
            self.market.configuration_market_information_state("218410").last_price,
        )

    def test_new_target_requests_snapshot_and_removed_target_rejects_pending_result(self) -> None:
        self.market.sync_monitoring_targets(("005930",))
        self.market.sync_monitoring_targets(("005930", "006400"))
        self.assertEqual(
            ("006400",),
            self.owner.kiwoom_api.request_initial_market_snapshot.call_args.args[0],
        )

        self.market.sync_monitoring_targets(("005930",))
        self.market._on_initial_market_snapshot_result(
            self._snapshot_result(stock_code="006400", current_price=65000)
        )
        self.assertIsNone(self.market.initial_market_snapshot_state("006400"))

    def test_removed_valid_target_readd_starts_new_bounded_lifecycle(self) -> None:
        self.market.sync_monitoring_targets(("218410",))
        callback = self.owner.kiwoom_api.request_initial_market_snapshot.call_args.kwargs[
            "callback"
        ]
        callback(self._snapshot_result(stock_code="218410", current_price=45_000))
        self.assertIsNotNone(self.market.initial_market_snapshot_state("218410"))

        self.market.sync_monitoring_targets(())
        self.assertIsNone(self.market.initial_market_snapshot_state("218410"))
        self.assertNotIn(
            "218410", self.market._initial_snapshot_request_attempts_by_stock
        )
        self.market.sync_monitoring_targets(("218410",))

        self.assertEqual(
            2, self.owner.kiwoom_api.request_initial_market_snapshot.call_count
        )
        self.assertEqual(
            1,
            self.market._initial_snapshot_request_attempts_by_stock["218410"],
        )

    def test_session_transition_still_drops_last_known_for_removed_target(self) -> None:
        self.market.sync_monitoring_targets(("005930",))
        self.market._on_initial_market_snapshot_result(self._snapshot_result())
        self.assertEqual(
            70_000,
            self.market.monitoring_market_information_state("005930").last_price,
        )
        self.owner.kiwoom_api.snapshot.connection_epoch = 8
        self.owner.kiwoom_api.snapshot.login_session_id = "SESSION-8"
        self.owner.kiwoom_api.broker_session_snapshot = Mock(
            return_value=SimpleNamespace(
                connected=True,
                connection_epoch=8,
                login_session_id="SESSION-8",
            )
        )

        self.market.sync_monitoring_targets(())

        self.assertIsNone(self.market.monitoring_market_information_state("005930"))

    def test_snapshot_populates_monitoring_projection_without_creating_a_tick(self) -> None:
        observed: list[dict[str, object]] = []
        self.market.market_data_observed.connect(observed.append)
        self.market.sync_monitoring_targets(("005930",))
        before = self.market.high_resolution_market_data_snapshot()
        self.market._on_initial_market_snapshot_result(self._snapshot_result())
        after = self.market.high_resolution_market_data_snapshot()
        state = self.market.monitoring_market_information_state("005930")

        self.assertEqual(70000, state.last_price)
        self.assertEqual((69000, 71000, 68000), (
            state.open_price, state.high_price, state.low_price
        ))
        self.assertEqual((1.25, -12.43, 117.2), (
            state.change_rate,
            state.previous_day_volume_rate,
            state.execution_strength,
        ))
        self.assertEqual(
            (before.received_tick_count, before.processed_tick_count),
            (after.received_tick_count, after.processed_tick_count),
        )
        self.assertEqual(0, after.current_queue_depth)
        self.assertIsNone(self.market.high_resolution_market_state("005930"))
        self.assertTrue(all(source == "SNAPSHOT" for _field, source in state.field_sources))
        self.assertEqual(
            ("005930", "INITIAL_SNAPSHOT", 7, "SESSION-7"),
            (
                observed[-1]["stock_code"],
                observed[-1]["source"],
                observed[-1]["connection_epoch"],
                observed[-1]["login_session_id"],
            ),
        )

    def test_realtime_fields_override_snapshot_and_blank_fields_keep_snapshot(self) -> None:
        observed: list[dict[str, object]] = []
        self.market.market_data_observed.connect(observed.append)
        self.market.sync_monitoring_targets(("005930",))
        self.market._on_initial_market_snapshot_result(self._snapshot_result())
        payload = self._raw_tick_payload(sequence=100, price=70100)
        payload.update(
            open_price=None,
            high_price=71200,
            low_price=None,
            change_rate=1.5,
            previous_day_volume_rate=None,
            execution_strength=None,
        )
        self.assertTrue(self.market._process_raw_realtime_tick(payload))

        state = self.market.monitoring_market_information_state("005930")
        self.assertEqual((70100, 69000, 71200, 68000), (
            state.last_price, state.open_price, state.high_price, state.low_price
        ))
        self.assertEqual((1.5, -12.43, 117.2), (
            state.change_rate,
            state.previous_day_volume_rate,
            state.execution_strength,
        ))
        self.assertEqual(
            {
                "last_price": "REALTIME",
                "open_price": "SNAPSHOT",
                "high_price": "REALTIME",
                "low_price": "SNAPSHOT",
                "change_rate": "REALTIME",
                "previous_day_volume_rate": "SNAPSHOT",
                "execution_strength": "SNAPSHOT",
            },
            dict(state.field_sources),
        )
        self.assertEqual("REALTIME", observed[-1]["source"])

    def test_high_resolution_state_rejects_stale_session_identity(self) -> None:
        self.market.sync_monitoring_targets(("005930",))
        self.assertTrue(
            self.market._process_raw_realtime_tick(
                self._raw_tick_payload(sequence=100, price=70100)
            )
        )
        self.market._realtime_shadow_session_identity = (8, "SESSION-8")

        self.assertIsNone(self.market.high_resolution_market_state("005930"))

    def test_late_snapshot_does_not_override_earlier_realtime(self) -> None:
        self.market.sync_monitoring_targets(("005930",))
        payload = self._raw_tick_payload(sequence=100, price=70100)
        payload.update(high_price=71200)
        self.assertTrue(self.market._process_raw_realtime_tick(payload))
        self.market._on_initial_market_snapshot_result(
            self._snapshot_result(current_price=70000, high_price=71000)
        )

        state = self.market.monitoring_market_information_state("005930")
        self.assertEqual((70100, 71200), (state.last_price, state.high_price))

    def test_reconnect_clears_old_snapshot_and_requests_current_targets_again(self) -> None:
        self.market.sync_monitoring_targets(("005930",))
        self.market._on_initial_market_snapshot_result(self._snapshot_result())
        self.owner.kiwoom_api.snapshot.connection_epoch = 8
        self.owner.kiwoom_api.snapshot.login_session_id = "SESSION-8"
        self.owner.kiwoom_api.broker_session_snapshot = Mock(
            return_value=SimpleNamespace(
                connected=True,
                connection_epoch=8,
                login_session_id="SESSION-8",
            )
        )

        self.market.sync_monitoring_targets(("005930",))

        self.assertIsNone(self.market.initial_market_snapshot_state("005930"))
        self.assertIsNone(
            self.market.fresh_monitoring_market_information_state("005930")
        )
        self.assertIsNone(
            self.market.configuration_market_information_state("005930")
        )
        last_known = self.market.monitoring_market_information_state("005930")
        self.assertEqual(
            (70000, 69000, 71000, 68000, 1.25, -12.43, 117.2),
            (
                last_known.last_price,
                last_known.open_price,
                last_known.high_price,
                last_known.low_price,
                last_known.change_rate,
                last_known.previous_day_volume_rate,
                last_known.execution_strength,
            ),
        )
        display_window = SimpleNamespace(
            main_monitoring_auto_trade_operation_host=lambda: SimpleNamespace(
                monitoring_market_information_state=(
                    self.market.monitoring_market_information_state
                ),
                fresh_monitoring_market_information_state=(
                    self.market.fresh_monitoring_market_information_state
                ),
            )
        )
        self.assertEqual(
            70_000,
            main_loader.main_stock_current_price(
                display_window,
                {"code": "005930", "name": "삼성전자"},
                {"holding_qty": 10},
            ),
        )
        tooltip = main_loader.main_stock_row_tooltip_from_projection(
            {
                "market": "KOSPI",
                "stock_code": "005930",
                "stock_name": "삼성전자",
                "operator_status": "대기",
            },
            last_known,
        )
        for expected in (
            "현재가 70,000",
            "시가 69,000",
            "고가 71,000",
            "저가 68,000",
            "등락률 +1.25%",
            "전일대비 -12.43%",
            "체결강도 117.2",
        ):
            self.assertIn(expected, tooltip)
        self.assertEqual(
            2,
            self.owner.kiwoom_api.request_initial_market_snapshot.call_count,
        )

        self.market._on_initial_market_snapshot_result(
            self._snapshot_result(
                current_price=72_000,
                epoch=8,
                session_id="SESSION-8",
            )
        )
        current_display = self.market.monitoring_market_information_state("005930")
        self.assertEqual((8, "SESSION-8", 72_000), (
            current_display.connection_epoch,
            current_display.login_session_id,
            current_display.last_price,
        ))
        self.assertEqual(
            "SNAPSHOT",
            dict(current_display.field_sources)["last_price"],
        )
        self.assertIsNone(
            self.market.fresh_monitoring_market_information_state("005930")
        )

    @staticmethod
    def _snapshot_result(
        *,
        stock_code: str = "005930",
        current_price: object = 70000,
        high_price: int = 71000,
        epoch: int = 7,
        session_id: str = "SESSION-7",
        rows: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        snapshot_rows = rows if rows is not None else [
            {
                "stock_code": stock_code,
                "current_price": current_price,
                "open_price": 69000,
                "high_price": high_price,
                "low_price": 68000,
                "change_rate": 1.25,
                "previous_day_volume_rate": -12.43,
                "execution_strength": 117.2,
                "cumulative_volume": 123456,
            }
        ]
        return {
            "ok": True,
            "type": "initial_market_snapshot",
            "stock_codes": [stock_code],
            "connection_epoch": epoch,
            "login_session_id": session_id,
            "snapshot_received_at": datetime.now().astimezone().isoformat(),
            "rows": snapshot_rows,
        }

    def test_stale_session_tick_does_not_mutate_current_state(self) -> None:
        self.market._realtime_shadow_session_identity = (11, "SESSION-11")
        current = self._raw_tick_payload(
            sequence=200,
            price=80000,
            epoch=11,
            session_id="SESSION-11",
        )
        self.assertTrue(self.market._process_raw_realtime_tick(current))
        stale = self._raw_tick_payload(
            sequence=201,
            price=1,
            epoch=10,
            session_id="SESSION-10",
        )
        scheduled = []
        with patch(
            "gui_market_data_host.QTimer.singleShot",
            side_effect=lambda _ms, callback: scheduled.append(callback),
        ):
            self.owner.kiwoom_api.realtime_shadow_tick_received.emit(stale)
            self.assertEqual(1, self.market.high_resolution_market_data_snapshot().current_queue_depth)
            scheduled.pop(0)()

        state = self.market.high_resolution_market_state("005930")
        self.assertEqual((80000, 200), (state.last_price, state.last_receive_sequence))

    def test_duplicate_or_older_sequence_does_not_regress_state(self) -> None:
        self._activate_raw_session()
        self.assertTrue(
            self.market._process_raw_realtime_tick(
                self._raw_tick_payload(sequence=100, price=70000)
            )
        )
        for sequence in (99, 100):
            self.assertFalse(
                self.market._process_raw_realtime_tick(
                    self._raw_tick_payload(sequence=sequence, price=1)
                )
            )

        state = self.market.high_resolution_market_state("005930")
        self.assertEqual((70000, 100), (state.last_price, state.last_receive_sequence))

    def test_raw_tick_metrics_and_overflow_are_observable(self) -> None:
        self._activate_raw_session()
        self.market.MAX_RAW_TICK_QUEUE_DEPTH = 2
        scheduled = []
        with patch(
            "gui_market_data_host.QTimer.singleShot",
            side_effect=lambda _ms, callback: scheduled.append(callback),
        ):
            for sequence in (1, 2, 3):
                self.owner.kiwoom_api.realtime_shadow_tick_received.emit(
                    self._raw_tick_payload(sequence=sequence, price=sequence)
                )
            queued = self.market.high_resolution_market_data_snapshot()
            self.assertEqual((3, 0), (queued.received_tick_count, queued.processed_tick_count))
            self.assertEqual((2, 2), (queued.current_queue_depth, queued.queue_high_watermark))
            self.assertEqual(1, queued.overflow_count)
            self.assertEqual("UNCERTAIN", queued.data_quality)
            scheduled.pop(0)()

        drained = self.market.high_resolution_market_data_snapshot()
        state = self.market.high_resolution_market_state("005930")
        self.assertEqual((3, 2, 0), (
            drained.received_tick_count,
            drained.processed_tick_count,
            drained.current_queue_depth,
        ))
        self.assertEqual((2, 2), (
            state.last_receive_sequence,
            state.processed_tick_count,
        ))
        self.assertEqual("UNCERTAIN", state.data_quality)

    def test_raw_tick_snapshot_reports_registration_and_processing_latency(self) -> None:
        self._activate_raw_session()
        payload = self._raw_tick_payload(sequence=100)
        with patch(
            "gui_market_data_host.monotonic",
            return_value=float(payload["received_monotonic"]) + 0.012,
        ):
            self.assertTrue(self.market._process_raw_realtime_tick(payload))

        snapshot = self.market.high_resolution_market_data_snapshot()
        self.assertTrue(snapshot.broker_connected)
        self.assertTrue(snapshot.realtime_registration_active)
        self.assertEqual(1, snapshot.realtime_target_stock_count)
        self.assertAlmostEqual(12.0, snapshot.last_processing_latency_ms, places=6)
        self.assertAlmostEqual(12.0, snapshot.max_processing_latency_ms, places=6)

    def test_public_market_state_is_an_independent_immutable_snapshot(self) -> None:
        self._activate_raw_session()
        self.market._process_raw_realtime_tick(
            self._raw_tick_payload(sequence=1, price=70000)
        )
        returned = self.market.high_resolution_market_state("005930")
        object.__setattr__(returned, "last_price", 1)

        self.assertEqual(
            70000,
            self.market.high_resolution_market_state("005930").last_price,
        )

    def _activate_raw_session(self) -> None:
        self.market._realtime_shadow_session_identity = (7, "SESSION-7")

    @staticmethod
    def _raw_tick_payload(
        *,
        stock_code: str = "005930",
        sequence: int = 1,
        price: int = 70000,
        epoch: int = 7,
        session_id: str = "SESSION-7",
    ) -> dict[str, object]:
        return {
            "stock_code": stock_code,
            "real_type": "stock_execution",
            "execution_time_raw": "101501",
            "current_price": price,
            "cumulative_volume": 123456,
            "trade_volume_raw": 10,
            "trade_volume_abs": 10,
            "received_at": "2026-08-20T10:15:01.000001+09:00",
            "received_monotonic": float(sequence),
            "receive_sequence": sequence,
            "market_datetime": "2026-08-20T10:15:01+09:00",
            "minute_key": "2026-08-20 10:15",
            "connection_epoch": epoch,
            "login_session_id": session_id,
        }

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
