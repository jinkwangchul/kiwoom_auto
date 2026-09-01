from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from execution_universe import (
    NOT_CURRENT_SESSION_PARTICIPANT,
    RECOVERY_NOT_READY,
    project_execution_universe,
)
from gui_auto_trade_timer import (
    auto_trade_real_execution_active,
    auto_trade_signal_probe_only_active,
)
from tests.participant_owner_fixture import attach_participant_owner, participant_owner


def _write_stock(
    root: Path,
    folder_name: str,
    *,
    state: dict[str, object],
    config: dict[str, object] | None = None,
) -> Path:
    stock_dir = root / folder_name
    stock_dir.mkdir()
    (stock_dir / "state.json").write_text(
        json.dumps(state),
        encoding="utf-8",
    )
    (stock_dir / "config.json").write_text(
        json.dumps(config or {}),
        encoding="utf-8",
    )
    return stock_dir


class ExecutionUniverseTest(unittest.TestCase):
    def test_trade_enabled_without_current_session_participant_is_not_member(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock_dir = _write_stock(
                root,
                "005930_Samsung",
                state={
                    "status": "RUNNING",
                    "trade_enabled": True,
                    "real_trade_enabled": True,
                },
            )
            window = SimpleNamespace(
                _main_monitoring_auto_trade_operation_host=participant_owner()
            )
            window.startup_recovery_session_ready = lambda refresh=False: True

            snapshot = project_execution_universe(window, stock_dirs=[stock_dir])

        entry = snapshot.entries[0]
        self.assertFalse(entry.participant)
        self.assertFalse(entry.execution_member)
        self.assertFalse(entry.execution_ready)
        self.assertIn(NOT_CURRENT_SESSION_PARTICIPANT, entry.blockers)
        self.assertEqual((), snapshot.execution_stock_codes)

    def test_recovery_not_ready_blocks_readiness_without_clearing_membership(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock_dir = _write_stock(
                root,
                "005930_Samsung",
                state={
                    "status": "RUNNING",
                    "trade_enabled": True,
                    "real_trade_enabled": True,
                },
            )
            window = SimpleNamespace(
                _main_monitoring_auto_trade_operation_host=participant_owner({"005930"})
            )
            window.startup_recovery_session_ready = lambda refresh=False: False

            snapshot = project_execution_universe(window, stock_dirs=[stock_dir])

        entry = snapshot.entries[0]
        self.assertTrue(entry.execution_member)
        self.assertFalse(entry.execution_ready)
        self.assertEqual(("005930",), snapshot.participant_stock_codes)
        self.assertEqual((RECOVERY_NOT_READY,), snapshot.global_blockers)
        self.assertEqual((), snapshot.execution_stock_codes)

    def test_timer_active_predicates_use_execution_ready_universe(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock_dir = _write_stock(
                root,
                "005930_Samsung",
                state={
                    "status": "RUNNING",
                    "trade_enabled": True,
                    "real_trade_enabled": True,
                    "signal_probe_only": True,
                },
            )
            window = SimpleNamespace(
                _main_monitoring_auto_trade_operation_host=participant_owner({"005930"})
            )
            window.startup_recovery_session_ready = lambda refresh=False: True

            with patch(
                "execution_universe.all_registered_stock_dirs",
                return_value=[stock_dir],
            ):
                self.assertTrue(auto_trade_signal_probe_only_active(window))
                self.assertFalse(auto_trade_real_execution_active(window))

            attach_participant_owner(window)
            with patch(
                "execution_universe.all_registered_stock_dirs",
                return_value=[stock_dir],
            ):
                self.assertFalse(auto_trade_signal_probe_only_active(window))
                self.assertFalse(auto_trade_real_execution_active(window))

    def test_registered_operation_targets_source_limits_execution_universe(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            assigned = _write_stock(
                root,
                "005930_Assigned",
                state={
                    "status": "RUNNING",
                    "trade_enabled": True,
                    "real_trade_enabled": True,
                },
            )
            unassigned = _write_stock(
                root,
                "492500_Unassigned",
                state={
                    "status": "RUNNING",
                    "trade_enabled": True,
                    "real_trade_enabled": True,
                },
            )
            window = SimpleNamespace(
                registered_operation_targets=lambda: [(assigned, "005930", "Assigned")],
                _main_monitoring_auto_trade_operation_host=participant_owner({"005930"}),
            )
            window.startup_recovery_session_ready = lambda refresh=False: True

            with patch(
                "execution_universe.all_registered_stock_dirs",
                return_value=[assigned, unassigned],
            ):
                snapshot = project_execution_universe(window)

        self.assertEqual(("005930",), snapshot.execution_stock_codes)
        self.assertEqual([assigned], [entry.stock_dir for entry in snapshot.entries])


if __name__ == "__main__":
    unittest.main()
