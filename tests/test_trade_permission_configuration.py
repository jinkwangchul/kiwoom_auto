import json
import io
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from execution_universe import project_execution_universe
import gui_auto_trade_context_menu as context_menu
import gui_auto_trade_setting_window as setting_window
import gui_main_stock_context_menu as main_context_menu
import gui_routine_service as routine_service
import real_order_preflight_reader
from gui_auto_trade_timer import (
    auto_trade_real_execution_active,
    auto_trade_signal_probe_only_active,
    _process_pending_signal_pipeline,
)
import state_policy
from tests.participant_owner_fixture import participant_owner


def _write_stock(
    root: Path,
    name: str = "012210_삼미금속",
    *,
    state: dict[str, object] | None = None,
    config: dict[str, object] | None = None,
) -> Path:
    stock_dir = root / "stocks" / name
    stock_dir.mkdir(parents=True)
    base_config = {
        "routine": "지표추종매매",
        "routine_instance_name": "지표추종매매A",
        "assigned_routine_instance_id": "inst-a",
        "operation_excluded": False,
    }
    if config:
        base_config.update(config)
    (stock_dir / "config.json").write_text(
        json.dumps(base_config, ensure_ascii=False),
        encoding="utf-8",
    )
    base_state = {"status": "STOPPED", "trade_enabled": False}
    if state:
        base_state.update(state)
    (stock_dir / "state.json").write_text(
        json.dumps(base_state, ensure_ascii=False),
        encoding="utf-8",
    )
    return stock_dir


class TradePermissionConfigurationTest(unittest.TestCase):
    def test_retired_ui_and_writer_symbols_are_absent(self) -> None:
        for owner, names in (
            (
                routine_service,
                (
                    "_patch_real_trade_enabled",
                    "ensure_single_real_trade_routine_for_stock",
                    "set_stock_real_trade_enabled",
                    "execute_selected_stock_real_trade_command",
                    "selected_stock_trade_permission_label",
                ),
            ),
            (state_policy, ("real_trade_enabled", "trade_permission_display")),
            (
                main_context_menu,
                ("toggle_main_monitoring_trade_permission",),
            ),
        ):
            for name in names:
                with self.subTest(owner=owner.__name__, name=name):
                    self.assertFalse(hasattr(owner, name))
        for name in (
            "selected_trade_permission_context_label",
            "selected_trade_permission_available",
            "toggle_selected_trade_permission",
        ):
            self.assertFalse(hasattr(setting_window.AutoTradeSettingWindow, name))
        self.assertNotIn(
            "trade_permission_allowed",
            context_menu.StockContextMenuAvailability.__dataclass_fields__,
        )
        for name in (
            "trade_permission_label",
            "trade_permission_available",
            "toggle_trade_permission",
        ):
            self.assertNotIn(name, context_menu.StockContextMenuCallbacks.__dataclass_fields__)

    def test_default_config_does_not_recreate_retired_permission(self) -> None:
        self.assertNotIn("real_trade_enabled", routine_service.default_config())

    def test_preflight_reader_omits_retired_permission_but_keeps_safety_fields(self) -> None:
        guard = {
            "real_trade_enabled": True,
            "kiwoom_logged_in": True,
            "account_selected": True,
            "operator_confirmed": True,
            "account_no": "12345678",
        }
        output = io.StringIO()
        with patch.object(
            real_order_preflight_reader,
            "read_json",
            side_effect=[{"orders": []}, guard],
        ), redirect_stdout(output):
            real_order_preflight_reader.main()

        text = output.getvalue()
        self.assertNotIn("real_trade_enabled", text)
        for field in (
            "kiwoom_logged_in",
            "account_selected",
            "operator_confirmed",
            "account_no",
        ):
            self.assertIn(field, text)

    def test_current_schema_uses_single_production_execution_universe(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = _write_stock(
                Path(temp),
                state={
                    "status": "MONITORING",
                    "trade_enabled": True,
                    "trade_started_at": "2026-08-26 09:30:00",
                    "signal_probe_only": False,
                },
            )
            window = SimpleNamespace(
                _main_monitoring_auto_trade_operation_host=participant_owner({"012210"}),
                startup_recovery_session_ready=lambda refresh=False: True,
                registered_operation_targets=lambda: [(stock_dir, "012210", "삼미금속")],
                statusBarMessage=lambda _message: None,
            )
            snapshot = project_execution_universe(window, stock_dirs=[stock_dir])

            with patch(
                "gui_auto_trade_timer.consume_pending_routine_signals_dry_run",
                Mock(return_value={"summary": {"signals_checked": 1}}),
            ) as consumer:
                pipeline = _process_pending_signal_pipeline(window, snapshot)

        entry = snapshot.entries[0]
        self.assertTrue(entry.participant)
        self.assertTrue(entry.persisted_trade_started)
        self.assertTrue(entry.execution_member)
        self.assertTrue(entry.execution_ready)
        self.assertFalse(hasattr(entry, "real_trade_enabled"))
        self.assertFalse(entry.signal_probe_only)
        self.assertTrue(auto_trade_real_execution_active(window, snapshot))
        self.assertFalse(auto_trade_signal_probe_only_active(window, snapshot))
        self.assertEqual(1, pipeline["signals_checked"])
        consumer.assert_called_once()


if __name__ == "__main__":
    unittest.main()
