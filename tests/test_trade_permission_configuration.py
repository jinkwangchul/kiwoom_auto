import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from execution_universe import project_execution_universe
from gui_routine_service import (
    ensure_single_real_trade_routine_for_stock,
    set_stock_real_trade_enabled,
)
from gui_auto_trade_timer import (
    auto_trade_real_execution_active,
    auto_trade_signal_probe_only_active,
    _process_pending_signal_pipeline,
)
from state_policy import real_trade_enabled, trade_permission_display
from tests.participant_owner_fixture import attach_participant_owner, participant_owner


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
        "real_trade_enabled": True,
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
    def test_default_display_is_real_trade(self) -> None:
        self.assertTrue(real_trade_enabled({}))
        self.assertEqual("실주문", trade_permission_display({})[0])

    def test_stopped_true_to_false_persists_only_permission_field(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = _write_stock(Path(temp))
            before = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))
            result = set_stock_real_trade_enabled(
                SimpleNamespace(
                    _main_monitoring_auto_trade_operation_host=participant_owner()
                ),
                stock_dir,
                "012210",
                "삼미금속",
                False,
            )
            saved = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))

        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertIs(saved["real_trade_enabled"], False)
        self.assertEqual("감시전용", trade_permission_display(saved)[0])
        for key in ("routine", "routine_instance_name", "assigned_routine_instance_id"):
            self.assertEqual(before[key], saved[key])

    def test_stopped_false_to_true_uses_existing_uniqueness_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = _write_stock(
                root,
                "012210_삼미금속",
                config={"real_trade_enabled": False, "routine_instance_name": "A"},
            )
            second = _write_stock(
                root,
                "005070_삼미금속B",
                config={"real_trade_enabled": True, "routine_instance_name": "B"},
            )
            with patch(
                "gui_routine_service.assigned_runtime_dirs_for_stock",
                return_value=[("A", first), ("B", second)],
            ):
                result = set_stock_real_trade_enabled(
                    SimpleNamespace(
                        _main_monitoring_auto_trade_operation_host=participant_owner()
                    ),
                    first,
                    "012210",
                    "삼미금속",
                    True,
                )
            first_saved = json.loads((first / "config.json").read_text(encoding="utf-8"))
            second_saved = json.loads((second / "config.json").read_text(encoding="utf-8"))

        self.assertTrue(result["ok"])
        self.assertIs(first_saved["real_trade_enabled"], True)
        self.assertIs(second_saved["real_trade_enabled"], False)

    def test_running_change_is_blocked_without_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = _write_stock(
                Path(temp),
                state={"status": "RUNNING", "trade_enabled": True},
            )
            before = (stock_dir / "config.json").read_text(encoding="utf-8")
            result = set_stock_real_trade_enabled(
                SimpleNamespace(
                    _main_monitoring_auto_trade_operation_host=participant_owner({"012210"})
                ),
                stock_dir,
                "012210",
                "삼미금속",
                False,
            )
            after = (stock_dir / "config.json").read_text(encoding="utf-8")

        self.assertFalse(result["ok"])
        self.assertEqual(before, after)

    def test_refresh_invariant_allows_zero_real_trade_routines(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = _write_stock(
                Path(temp),
                config={"real_trade_enabled": False, "routine_instance_name": "A"},
            )
            with patch(
                "gui_routine_service.assigned_runtime_dirs_for_stock",
                return_value=[("A", stock_dir)],
            ):
                selected = ensure_single_real_trade_routine_for_stock("012210", "삼미금속")
            saved = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))

        self.assertEqual("", selected)
        self.assertIs(saved["real_trade_enabled"], False)

    def test_monitoring_only_operation_is_execution_ready_without_real_executor(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = _write_stock(
                Path(temp),
                state={
                    "status": "MONITORING",
                    "trade_enabled": True,
                    "trade_started_at": "2026-08-26 09:30:00",
                    "real_trade_enabled": False,
                    "signal_probe_only": False,
                },
                config={"real_trade_enabled": False},
            )
            window = SimpleNamespace(
                _main_monitoring_auto_trade_operation_host=participant_owner({"012210"}),
                startup_recovery_session_ready=lambda refresh=False: True,
                registered_operation_targets=lambda: [(stock_dir, "012210", "삼미금속")],
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
        self.assertFalse(entry.real_trade_enabled)
        self.assertFalse(entry.signal_probe_only)
        self.assertFalse(auto_trade_real_execution_active(window, snapshot))
        self.assertFalse(auto_trade_signal_probe_only_active(window, snapshot))
        self.assertEqual({}, pipeline)
        consumer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
