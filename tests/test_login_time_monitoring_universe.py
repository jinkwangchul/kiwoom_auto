from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from gui_auto_trade_operation_host import AutoTradeOperationHost
from gui_operation_ui_context import sync_auto_trade_monitoring_universe
from gui_windows import MainWindow
from stock_repository import StockRecord, StockRepository


def _record(code: str, *, instance_id: str = "") -> StockRecord:
    return StockRecord(
        code=code,
        name=code,
        routine="",
        enabled=True,
        stock_path=f"stocks/{code}_{code}",
        assigned_routine_instance_id=instance_id,
    )


class MonitoringUniverseProjectionTests(unittest.TestCase):
    def test_current_registered_stocks_are_deduped_and_unsupported_is_isolated(self) -> None:
        repository = StockRepository()
        with patch.object(
            repository,
            "list_stocks",
            return_value=[
                _record("005930", instance_id="INSTANCE-1"),
                _record("005930", instance_id="INSTANCE-1"),
                _record("ABC123", instance_id="INSTANCE-1"),
                _record("006400"),
                _record("000660", instance_id="MISSING-INSTANCE"),
            ],
        ), patch(
            "stock_repository.load_persisted_routine_instances",
            return_value=[SimpleNamespace(instance_id="INSTANCE-1")],
        ):
            projection = repository.realtime_monitoring_universe()

        self.assertEqual(("005930",), projection.target_stock_codes)
        self.assertEqual(("ABC123",), projection.unsupported_stock_codes)
        self.assertEqual(3, projection.source_record_count)

    def test_stopped_review_current_assignment_remains_registered(self) -> None:
        repository = StockRepository()
        records = [
            _record("005930", instance_id="INSTANCE-1"),
            _record("006400", instance_id="INSTANCE-1"),
        ]
        with patch.object(repository, "list_stocks", return_value=records), patch(
            "stock_repository.load_persisted_routine_instances",
            return_value=[SimpleNamespace(instance_id="INSTANCE-1")],
        ):
            current = repository.list_current_registered_stocks()

        self.assertEqual(["005930", "006400"], [item.code for item in current])

    def test_operation_host_entry_is_read_only_thin_projection(self) -> None:
        projection = SimpleNamespace(
            target_stock_codes=("005930", "006400"),
            unsupported_stock_codes=("ABC123",),
            source_record_count=3,
        )
        market = SimpleNamespace(
            sync_monitoring_targets=Mock(
                return_value={"ok": True, "changed": True, "active": True}
            )
        )
        host = SimpleNamespace(_market_data_host=market)
        with patch(
            "gui_auto_trade_operation_host.StockRepository"
        ) as repository_type:
            repository_type.return_value.realtime_monitoring_universe.return_value = projection
            result = AutoTradeOperationHost.sync_monitoring_universe_for_current_session(host)

        market.sync_monitoring_targets.assert_called_once_with(("005930", "006400"))
        self.assertEqual(("ABC123",), result["unsupported_stock_codes"])


class LoginTimeMonitoringIntegrationTests(unittest.TestCase):
    @staticmethod
    def _main_double(events: list[str]):
        host = SimpleNamespace(
            sync_monitoring_universe_for_current_session=Mock(
                side_effect=lambda: events.append("monitoring") or {"ok": True}
            )
        )
        status_bar = SimpleNamespace(showMessage=Mock())
        owner = SimpleNamespace(
            _event_journal_kiwoom_connected=False,
            _handled_kiwoom_login_identity=None,
            _account_authentication_states={},
            _account_query_states={},
            login_status_label=SimpleNamespace(setText=Mock()),
            _apply_connected_kiwoom_login_button_state=Mock(),
            _apply_kiwoom_login_button_state=Mock(),
            refresh_kiwoom_accounts=Mock(),
            sync_account_funds_selection=Mock(),
            main_monitoring_auto_trade_operation_host=Mock(return_value=host),
            request_account_funds=Mock(
                side_effect=lambda: events.append("account_funds")
            ),
            start_production_recovery=Mock(
                side_effect=lambda: events.append("recovery")
            ),
            start_stock_library_sync_for_current_session=Mock(),
            statusBar=Mock(return_value=status_bar),
        )
        return owner, host

    def test_new_login_session_syncs_monitoring_before_recovery(self) -> None:
        events: list[str] = []
        owner, host = self._main_double(events)
        scheduled: list[object] = []
        with patch(
            "gui_windows.QTimer.singleShot",
            side_effect=lambda _delay, callback: scheduled.append(callback),
        ) as single_shot, patch(
            "gui_windows.append_production_event"
        ):
            MainWindow.on_kiwoom_login_state_changed(
                owner,
                {
                    "connected": True,
                    "connection_epoch": 7,
                    "login_session_id": "SESSION-7",
                },
            )
            self.assertEqual(["monitoring"], events)
            self.assertEqual(1, len(scheduled))
            self.assertEqual(500, single_shot.call_args.args[0])
            scheduled.pop(0)()

        host.sync_monitoring_universe_for_current_session.assert_called_once_with()
        self.assertLess(events.index("monitoring"), events.index("recovery"))
        self.assertLess(events.index("account_funds"), events.index("recovery"))

    def test_login_failure_never_registers_monitoring(self) -> None:
        events: list[str] = []
        owner, host = self._main_double(events)
        owner._stop_production_recovery_timers = Mock()
        owner._production_recovery_status_result = Mock()
        with patch("gui_windows.append_production_event"):
            MainWindow.on_kiwoom_login_state_changed(
                owner,
                {"connected": False, "message": "failed"},
            )

        host.sync_monitoring_universe_for_current_session.assert_not_called()

    def test_automatic_retention_runs_once_for_each_new_login_session(self) -> None:
        events: list[str] = []
        owner, _host = self._main_double(events)
        retention_runner = SimpleNamespace(run_for_session=Mock())
        owner.stock_library_diagnostics_retention = retention_runner
        first_state = {
            "connected": True,
            "connection_epoch": 7,
            "login_session_id": "SESSION-7",
        }
        second_state = {
            "connected": True,
            "connection_epoch": 8,
            "login_session_id": "SESSION-8",
        }

        with patch("gui_windows.QTimer.singleShot"), patch(
            "gui_windows.append_production_event"
        ):
            for _ in range(10):
                MainWindow.on_kiwoom_login_state_changed(owner, first_state)
            MainWindow.on_kiwoom_login_state_changed(owner, second_state)

        self.assertEqual(
            [
                call(
                    current_connection_epoch=7,
                    current_session_id="SESSION-7",
                ),
                call(
                    current_connection_epoch=8,
                    current_session_id="SESSION-8",
                ),
            ],
            retention_runner.run_for_session.call_args_list,
        )

    def test_automatic_retention_exception_does_not_change_login_success(self) -> None:
        events: list[str] = []
        owner, host = self._main_double(events)
        owner.stock_library_diagnostics_retention = SimpleNamespace(
            run_for_session=Mock(side_effect=RuntimeError("cleanup failed"))
        )
        scheduled: list[object] = []

        with patch(
            "gui_windows.QTimer.singleShot",
            side_effect=lambda _delay, callback: scheduled.append(callback),
        ), patch("gui_windows.append_production_event"):
            MainWindow.on_kiwoom_login_state_changed(
                owner,
                {
                    "connected": True,
                    "connection_epoch": 9,
                    "login_session_id": "SESSION-9",
                },
            )

        self.assertEqual((9, "SESSION-9"), owner._handled_kiwoom_login_identity)
        self.assertTrue(owner._event_journal_kiwoom_connected)
        self.assertEqual(1, len(scheduled))
        host.sync_monitoring_universe_for_current_session.assert_called_once_with()

    def test_view_refresh_does_not_resync_monitoring(self) -> None:
        host = SimpleNamespace(
            sync_monitoring_universe_for_current_session=Mock(return_value={"ok": True})
        )
        owner = SimpleNamespace(
            main_monitoring_auto_trade_operation_host=Mock(return_value=host),
            refresh_all=Mock(),
            auto_trade_setting_window=None,
        )
        MainWindow.refresh_auto_trade_assignment_views(owner)
        host.sync_monitoring_universe_for_current_session.assert_not_called()
        owner.refresh_all.assert_called_once_with()

    def test_explicit_membership_sync_uses_operation_host_once(self) -> None:
        host = SimpleNamespace(
            sync_monitoring_universe_for_current_session=Mock(
                return_value={"ok": True, "changed": True}
            )
        )
        owner = SimpleNamespace(
            main_monitoring_auto_trade_operation_host=Mock(return_value=host),
        )

        result = sync_auto_trade_monitoring_universe(owner)

        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        host.sync_monitoring_universe_for_current_session.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
