from __future__ import annotations

import json
from pathlib import Path
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
        self.assertEqual(
            ("005930", "ABC123"),
            projection.initial_snapshot_target_stock_codes,
        )
        self.assertEqual(("ABC123",), projection.unsupported_stock_codes)
        self.assertEqual(3, projection.source_record_count)

    def test_alphanumeric_stock_is_snapshot_target_but_not_realtime_target(self) -> None:
        repository = StockRepository()
        codes = ("000080", "0009K0", "005380", "032680")
        with patch.object(
            repository,
            "list_stocks",
            return_value=[
                _record(code, instance_id="INSTANCE-1") for code in codes
            ],
        ), patch(
            "stock_repository.load_persisted_routine_instances",
            return_value=[SimpleNamespace(instance_id="INSTANCE-1")],
        ):
            projection = repository.realtime_monitoring_universe()

        self.assertEqual(
            ("000080", "005380", "032680"),
            projection.target_stock_codes,
        )
        self.assertEqual(
            ("000080", "0009K0", "005380", "032680"),
            projection.initial_snapshot_target_stock_codes,
        )
        self.assertEqual(("0009K0",), projection.unsupported_stock_codes)

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
            target_stock_codes=("000080", "005380", "032680"),
            initial_snapshot_target_stock_codes=(
                "000080",
                "0009K0",
                "005380",
                "032680",
            ),
            unsupported_stock_codes=("0009K0",),
            source_record_count=4,
        )
        market = SimpleNamespace(
            sync_monitoring_targets=Mock(
                return_value={"ok": True, "changed": True, "active": True}
            ),
            sync_candle_standby_requirements=Mock(return_value={
                "ok": True,
                "standby_stock_codes": (),
            }),
            refresh_operation_candles=Mock(return_value={"accepted": False}),
        )
        host = SimpleNamespace(_market_data_host=market)
        with patch(
            "gui_auto_trade_operation_host.StockRepository"
        ) as repository_type:
            repository_type.return_value.realtime_monitoring_universe.return_value = projection
            repository_type.return_value.list_current_registered_stocks.return_value = []
            result = AutoTradeOperationHost.sync_monitoring_universe_for_current_session(host)

        market.sync_monitoring_targets.assert_called_once_with(
            ("000080", "0009K0", "005380", "032680")
        )
        self.assertEqual(
            ("000080", "0009K0", "005380", "032680"),
            result["monitoring_target_stock_codes"],
        )
        self.assertEqual(
            ("000080", "005380", "032680"),
            result["realtime_target_stock_codes"],
        )
        self.assertEqual(("0009K0",), result["unsupported_stock_codes"])

    def test_login_sync_discovers_optional_routine_candle_requirements_and_bootstraps(self) -> None:
        records = [
            _record("005930", instance_id="INSTANCE-CANDLE"),
            _record("000660", instance_id="INSTANCE-FREE"),
            _record("035420", instance_id="INSTANCE-BROKEN"),
        ]
        projection = SimpleNamespace(
            target_stock_codes=("000660", "005930", "035420"),
            initial_snapshot_target_stock_codes=("000660", "005930", "035420"),
            unsupported_stock_codes=(),
            source_record_count=3,
        )
        instances = {
            "INSTANCE-CANDLE": SimpleNamespace(
                instance_id="INSTANCE-CANDLE",
                definition_id="candle",
                rules_path=Path("candle-rules.json"),
            ),
            "INSTANCE-FREE": SimpleNamespace(
                instance_id="INSTANCE-FREE",
                definition_id="free",
                rules_path=Path("free-rules.json"),
            ),
            "INSTANCE-BROKEN": SimpleNamespace(
                instance_id="INSTANCE-BROKEN",
                definition_id="broken",
                rules_path=Path("broken-rules.json"),
            ),
        }
        definitions = {
            "candle": SimpleNamespace(
                definition_id="candle",
                locators={"evaluation": {
                    "market_bar_projection_callable": "declare",
                }},
            ),
            "free": SimpleNamespace(
                definition_id="free",
                locators={"evaluation": {"callable": "evaluate"}},
            ),
            "broken": SimpleNamespace(
                definition_id="broken",
                locators={"evaluation": {
                    "market_bar_projection_callable": "declare",
                }},
            ),
        }
        repository = Mock()
        repository.realtime_monitoring_universe.return_value = projection
        repository.list_current_registered_stocks.return_value = records
        market = SimpleNamespace(
            sync_monitoring_targets=Mock(return_value={"ok": True}),
            sync_candle_standby_requirements=Mock(return_value={"ok": True}),
            refresh_operation_candles=Mock(return_value={"accepted": True}),
        )
        host = SimpleNamespace(_market_data_host=market)

        def load_callable(definition, _role, *, callable_key):
            self.assertEqual("market_bar_projection_callable", callable_key)
            if definition.definition_id == "broken":
                raise ValueError("bad routine declaration")
            return lambda _rules: {
                "projection": "FORMING_BASE_BAR",
                "warmup_bars": 35,
            }

        with patch("gui_auto_trade_operation_host.StockRepository", return_value=repository), patch(
            "routine_instance_registry.routine_instance_by_id",
            side_effect=lambda instance_id: instances.get(instance_id),
        ), patch(
            "routine_instance_registry.routine_definition_by_id",
            side_effect=lambda definition_id: definitions.get(definition_id),
        ), patch.object(
            Path,
            "read_text",
            return_value=json.dumps({"bar": {"bar_minutes": 5}}),
        ), patch(
            "routine_package_contract.load_routine_callable",
            side_effect=load_callable,
        ):
            result = AutoTradeOperationHost.sync_monitoring_universe_for_current_session(host)

        market.sync_candle_standby_requirements.assert_called_once_with(
            [{
                "stock_code": "005930",
                "rules": {"bar": {"bar_minutes": 5}},
                "projection_request": {
                    "projection": "FORMING_BASE_BAR",
                    "warmup_bars": 35,
                },
            }],
            preserve_stock_codes=("035420",),
        )
        market.refresh_operation_candles.assert_called_once()
        self.assertTrue(market.refresh_operation_candles.call_args.kwargs["drain_all"])
        self.assertEqual(("005930",), result["standby_requirement_stock_codes"])
        self.assertEqual(("000660",), result["standby_requirement_skipped_stock_codes"])
        self.assertEqual("035420", result["standby_requirement_errors"][0]["stock_code"])

    def test_login_sync_bootstraps_when_only_previous_failed_requirement_is_preserved(self) -> None:
        projection = SimpleNamespace(
            target_stock_codes=("035420",),
            initial_snapshot_target_stock_codes=("035420",),
            unsupported_stock_codes=(),
            source_record_count=1,
        )
        repository = Mock()
        repository.realtime_monitoring_universe.return_value = projection
        repository.list_current_registered_stocks.return_value = [
            _record("035420", instance_id="INSTANCE-BROKEN")
        ]
        instance = SimpleNamespace(
            instance_id="INSTANCE-BROKEN",
            definition_id="broken",
            rules_path=Path("broken-rules.json"),
        )
        definition = SimpleNamespace(
            definition_id="broken",
            locators={"evaluation": {"market_bar_projection_callable": "declare"}},
        )
        market = SimpleNamespace(
            sync_monitoring_targets=Mock(return_value={"ok": True}),
            sync_candle_standby_requirements=Mock(return_value={
                "ok": True,
                "standby_stock_codes": ("035420",),
                "preserved_stock_codes": ("035420",),
            }),
            refresh_operation_candles=Mock(return_value={"accepted": True}),
        )
        host = SimpleNamespace(_market_data_host=market)

        with patch("gui_auto_trade_operation_host.StockRepository", return_value=repository), patch(
            "routine_instance_registry.routine_instance_by_id",
            return_value=instance,
        ), patch(
            "routine_instance_registry.routine_definition_by_id",
            return_value=definition,
        ), patch.object(
            Path,
            "read_text",
            return_value=json.dumps({"bar": {"bar_minutes": 5}}),
        ), patch(
            "routine_package_contract.load_routine_callable",
            side_effect=ValueError("bad routine declaration"),
        ):
            result = AutoTradeOperationHost.sync_monitoring_universe_for_current_session(host)

        market.sync_candle_standby_requirements.assert_called_once_with(
            [],
            preserve_stock_codes=("035420",),
        )
        market.refresh_operation_candles.assert_called_once()
        self.assertTrue(market.refresh_operation_candles.call_args.kwargs["drain_all"])
        self.assertEqual((), result["standby_requirement_stock_codes"])
        self.assertEqual("035420", result["standby_requirement_errors"][0]["stock_code"])


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
