from __future__ import annotations

from dataclasses import fields
from pathlib import Path
import unittest

from execution_universe import ExecutionUniverseEntry
from gui_config_utils import default_config
from routine_instance_registry import RoutineInstanceRecord
from state_policy import start_status_by_operation_mode, status_after_operation_mode_change


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RealTradeBackendRetirementTest(unittest.TestCase):
    def test_new_config_has_no_legacy_permission_defaults(self) -> None:
        config = default_config()
        self.assertNotIn("real_trade_enabled", config)
        self.assertNotIn("real_trade_policy_updated_at", config)

    def test_start_status_is_independent_of_legacy_permission_value(self) -> None:
        base = {
            "operation_mode": "CONTINUOUS",
            "manual_start_time": "00:00",
            "manual_end_time": "23:59",
        }
        results = []
        changed_results = []
        for value in (True, False, None):
            config = dict(base)
            if value is not None:
                config["real_trade_enabled"] = value
            results.append(start_status_by_operation_mode(config))
            changed_results.append(status_after_operation_mode_change("CONTINUOUS", config))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])
        self.assertEqual(changed_results[0], changed_results[1])
        self.assertEqual(changed_results[1], changed_results[2])

    def test_removed_backend_dto_aliases_are_absent(self) -> None:
        self.assertNotIn("real_trade_enabled", {field.name for field in fields(ExecutionUniverseEntry)})
        self.assertNotIn("real_trade_allowed", {field.name for field in fields(RoutineInstanceRecord)})

    def test_active_backend_sources_have_no_retired_permission_consumers(self) -> None:
        files = (
            "gui_auto_trade_run_control.py",
            "gui_auto_trade_status_ops.py",
            "execution_universe.py",
            "gui_auto_trade_timer.py",
            "auto_trade_order_execution_boundary.py",
            "execution_approval_gate.py",
            "execution_approval_service.py",
            "execution_broker_dispatch_open_policy.py",
            "execution_final_send_gate_readiness_policy.py",
            "execution_preview_service.py",
            "execution_preview_reporter.py",
            "execution_readiness_validator.py",
            "final_execution_guard.py",
            "final_send_gate_service.py",
            "real_order_preflight.py",
            "real_order_preflight_service.py",
            "sell_common_execution_preview_adapter.py",
            "send_order_entrypoint.py",
            "sell_dispatch_final_guard_chain.py",
            "routine_instance_registry.py",
        )
        for name in files:
            with self.subTest(name=name):
                source = (PROJECT_ROOT / name).read_text(encoding="utf-8")
                self.assertNotIn("real_trade_enabled", source)
                self.assertNotIn("real_trade_guard_ok", source)
                self.assertNotIn("real_trade_allowed", source)


if __name__ == "__main__":
    unittest.main()
