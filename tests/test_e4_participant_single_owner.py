from __future__ import annotations

from types import SimpleNamespace
import unittest

from PyQt5.QtCore import QObject

from gui_auto_trade_operation_host import AutoTradeOperationHost
from gui_auto_trade_policy import (
    ParticipantOwnerUnavailableError,
    auto_trade_current_session_operation_participant_codes,
    auto_trade_register_current_session_operation_participants,
    auto_trade_retire_current_session_operation_participants,
    auto_trade_setting_current_session_trade_started,
)


class ParticipantSingleOwnerTest(unittest.TestCase):
    def _host(self) -> AutoTradeOperationHost:
        host = AutoTradeOperationHost(QObject())
        self.addCleanup(host.deleteLater)
        return host

    def test_host_owns_additive_snapshot_and_idempotent_retirement(self) -> None:
        host = self._host()

        self.assertEqual((), host.current_session_operation_participant_stock_codes())
        self.assertEqual(
            ("005930",),
            host.register_current_session_operation_participants(
                ("005930", "005930", "")
            ),
        )
        host.register_current_session_operation_participants(("000660",))
        snapshot = host.current_session_operation_participant_stock_codes()
        self.assertEqual(("000660", "005930"), snapshot)
        self.assertIsInstance(snapshot, tuple)

        first = host.retire_current_session_operation_participants(("005930",))
        second = host.retire_current_session_operation_participants(("005930",))
        self.assertEqual(("005930",), first["removed"])
        self.assertEqual(("000660",), first["remaining"])
        self.assertEqual((), second["removed"])
        self.assertEqual(("000660",), second["remaining"])

    def test_main_settings_and_adapter_resolve_only_the_host_snapshot(self) -> None:
        host = self._host()
        host.register_current_session_operation_participants(("035420",))
        main = SimpleNamespace(
            _main_monitoring_auto_trade_operation_host=host,
            legacy_participant_mirror={"005930"},
        )
        settings = SimpleNamespace(
            _owner=main,
            legacy_participant_mirror={"000660"},
        )
        adapter = SimpleNamespace(
            _window=main,
            legacy_participant_mirror={"051910"},
        )

        expected = ("035420",)
        self.assertEqual(
            expected,
            auto_trade_current_session_operation_participant_codes(main),
        )
        self.assertEqual(
            expected,
            auto_trade_current_session_operation_participant_codes(settings),
        )
        self.assertEqual(
            expected,
            auto_trade_current_session_operation_participant_codes(adapter),
        )

        reopened_settings = SimpleNamespace(_owner=main)
        self.assertEqual(
            expected,
            auto_trade_current_session_operation_participant_codes(reopened_settings),
        )

    def test_policy_writes_delegate_once_to_the_canonical_host(self) -> None:
        host = self._host()
        main = SimpleNamespace(_main_monitoring_auto_trade_operation_host=host)
        settings = SimpleNamespace(_owner=main)

        self.assertEqual(
            ("005930",),
            auto_trade_register_current_session_operation_participants(
                settings,
                ("005930",),
            ),
        )
        self.assertEqual(
            ("005930",),
            host.current_session_operation_participant_stock_codes(),
        )
        result = auto_trade_retire_current_session_operation_participants(
            main,
            ("005930",),
        )
        self.assertEqual(("005930",), result["removed"])
        self.assertEqual((), host.current_session_operation_participant_stock_codes())

    def test_owner_resolution_failure_is_fail_closed(self) -> None:
        with self.assertRaises(ParticipantOwnerUnavailableError) as caught:
            auto_trade_current_session_operation_participant_codes(SimpleNamespace())

        self.assertEqual(
            "OPERATION_HOST_OWNER_UNAVAILABLE",
            caught.exception.reason_code,
        )

    def test_durable_or_raw_running_state_does_not_promote_a_participant(self) -> None:
        host = self._host()
        main = SimpleNamespace(
            _main_monitoring_auto_trade_operation_host=host,
            operation_participant_stock_codes=("005930",),
            raw_status="RUNNING",
            trade_started=True,
        )

        self.assertEqual(
            (),
            auto_trade_current_session_operation_participant_codes(main),
        )
        self.assertFalse(
            auto_trade_setting_current_session_trade_started(
                main,
                True,
                "005930",
            )
        )

    def test_reconnect_and_review_metadata_do_not_clear_participation(self) -> None:
        host = self._host()
        host.register_current_session_operation_participants(("005930",))

        host._owner.connection_epoch = 2
        host._owner.login_session_id = "reconnected"
        host._owner.review_required = True

        self.assertTrue(host.is_current_session_operation_participant("005930"))
        self.assertEqual(
            ("005930",),
            host.current_session_operation_participant_stock_codes(),
        )


if __name__ == "__main__":
    unittest.main()
