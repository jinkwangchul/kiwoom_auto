from __future__ import annotations

import builtins
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from PyQt5.QtCore import QCoreApplication, QObject

from gui_market_data_host import MarketDataHost


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
        self.clear_realtime_shadow_registration = Mock(
            return_value={"ok": True, "changed": False, "active": False}
        )
        self.sync_realtime_shadow_registration = Mock()
        self.SetRealReg = Mock()
        self.SetRealRemove = Mock()
        self.CommRqData = Mock()


class PriceSignalObservationGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self) -> None:
        self.owner = QObject()
        self.api = _Api()
        self.market = MarketDataHost(self.owner, self.api, lambda _code: None)
        self.market._realtime_shadow_session_identity = (7, "SESSION-7")

    def tearDown(self) -> None:
        self.market.shutdown()

    def test_default_is_off_and_toggle_is_process_local(self) -> None:
        self.assertFalse(self.market.price_signal_observation_enabled())
        self.assertTrue(self.market.set_price_signal_observation_enabled(True))
        self.assertTrue(self.market.price_signal_observation_enabled())
        self.assertFalse(self.market.set_price_signal_observation_enabled(False))

    def test_toggle_has_no_persistent_write(self) -> None:
        with patch.object(Path, "write_text") as write_text, patch.object(
            Path, "write_bytes"
        ) as write_bytes, patch.object(builtins, "open") as open_file:
            self.market.set_price_signal_observation_enabled(True)
            self.market.set_price_signal_observation_enabled(False)

        write_text.assert_not_called()
        write_bytes.assert_not_called()
        open_file.assert_not_called()

    def test_off_keeps_market_state_but_suppresses_observation(self) -> None:
        observed = []
        self.market.high_resolution_price_observed.connect(observed.append)
        self._emit_and_drain(self._tick(sequence=1, price=70000))

        state = self.market.high_resolution_market_state("005930")
        self.assertEqual((70000, 1), (state.last_price, state.last_receive_sequence))
        self.assertEqual([], observed)

    def test_on_emits_exactly_one_immutable_state_per_normal_tick(self) -> None:
        observed = []
        self.market.high_resolution_price_observed.connect(observed.append)
        self.market.set_price_signal_observation_enabled(True)
        self._emit_and_drain(self._tick(sequence=1, price=70000))

        self.assertEqual(1, len(observed))
        self.assertEqual(("005930", 1, 70000), (
            observed[0].stock_code,
            observed[0].last_receive_sequence,
            observed[0].last_price,
        ))

    def test_enabling_does_not_replay_ticks_processed_while_off(self) -> None:
        observed = []
        self.market.high_resolution_price_observed.connect(observed.append)
        for sequence in (1, 2, 3):
            self._emit_and_drain(self._tick(sequence=sequence, price=sequence))

        self.market.set_price_signal_observation_enabled(True)
        self.assertEqual([], observed)
        self._emit_and_drain(self._tick(sequence=4, price=4))
        self.assertEqual([4], [state.last_receive_sequence for state in observed])

    def test_enabling_does_not_promote_an_off_tick_still_pending(self) -> None:
        observed = []
        scheduled = []
        self.market.high_resolution_price_observed.connect(observed.append)
        with patch(
            "gui_market_data_host.QTimer.singleShot",
            side_effect=lambda _ms, callback: scheduled.append(callback),
        ):
            self.api.realtime_shadow_tick_received.emit(self._tick(sequence=1))
            self.market.set_price_signal_observation_enabled(True)
            scheduled.pop(0)()

        self.assertEqual([], observed)
        self.assertEqual(1, self.market.high_resolution_market_state("005930").last_receive_sequence)

    def test_disabling_suppresses_an_on_tick_still_pending(self) -> None:
        observed = []
        scheduled = []
        self.market.high_resolution_price_observed.connect(observed.append)
        self.market.set_price_signal_observation_enabled(True)
        with patch(
            "gui_market_data_host.QTimer.singleShot",
            side_effect=lambda _ms, callback: scheduled.append(callback),
        ):
            self.api.realtime_shadow_tick_received.emit(self._tick(sequence=1))
            self.market.set_price_signal_observation_enabled(False)
            scheduled.pop(0)()

        self.assertEqual([], observed)
        self.assertEqual(1, self.market.high_resolution_market_state("005930").last_receive_sequence)

    def test_observation_preserves_uncertain_quality(self) -> None:
        observed = []
        self.market.high_resolution_price_observed.connect(observed.append)
        self.market._raw_tick_uncertain_stock_codes.add("005930")
        self.market.set_price_signal_observation_enabled(True)
        self._emit_and_drain(self._tick(sequence=1))

        self.assertEqual("UNCERTAIN", observed[0].data_quality)

    def test_toggle_does_not_touch_registration_or_tr_transport(self) -> None:
        for enabled in (True, False, True, False):
            self.market.set_price_signal_observation_enabled(enabled)

        self.api.sync_realtime_shadow_registration.assert_not_called()
        self.api.SetRealReg.assert_not_called()
        self.api.SetRealRemove.assert_not_called()
        self.api.CommRqData.assert_not_called()

    def _emit_and_drain(self, payload: dict[str, object]) -> None:
        scheduled = []
        with patch(
            "gui_market_data_host.QTimer.singleShot",
            side_effect=lambda _ms, callback: scheduled.append(callback),
        ):
            self.api.realtime_shadow_tick_received.emit(payload)
            self.assertEqual(1, len(scheduled))
            scheduled.pop(0)()

    @staticmethod
    def _tick(*, sequence: int, price: int = 70000) -> dict[str, object]:
        return {
            "stock_code": "005930",
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
            "connection_epoch": 7,
            "login_session_id": "SESSION-7",
        }


if __name__ == "__main__":
    unittest.main()
