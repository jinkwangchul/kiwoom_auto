from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import execution_universe
import gui_windows
from auto_trade_order_execution_boundary import AutoTradeOrderExecutionBoundary
from production_recovery_contract import RecoveryGateDecision
from real_trade_retirement_migration import (
    CURRENT_SCHEMA,
    DATA_INVALID,
    MIGRATION_REQUIRED,
    LegacySchemaCompatibilityResult,
    R6CleanupInventoryExpectation,
    RealTradeRetirementCleanup,
    inspect_real_trade_schema_compatibility,
)
from stock_repository import STOCK_CONFIG_DELETE_FIELD, StockRepository


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


class RealTradeRetirementCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self._write_runtime()
        self.stock_a = self._write_stock(
            "005930",
            "삼성전자",
            config_legacy=True,
            state_legacy=True,
            timestamp=True,
            assignment="instance-a",
            instance_enabled=False,
        )
        self.stock_b = self._write_stock(
            "000660",
            "SK하이닉스",
            operation_excluded=True,
        )
        self.expectation = R6CleanupInventoryExpectation(
            stock_count=2,
            config_true=1,
            config_timestamp_present=1,
            state_true=1,
        )

    def _write_runtime(self) -> None:
        for filename, key in (
            ("order_queue.json", "orders"),
            ("order_executions.json", "executions"),
            ("order_locks.json", "locks"),
            ("positions.json", "positions"),
            ("broker_holdings.json", "holdings"),
        ):
            _write_json(self.root / "runtime" / filename, {key: []})

    def _write_stock(
        self,
        code: str,
        name: str,
        *,
        config_legacy: object = STOCK_CONFIG_DELETE_FIELD,
        state_legacy: object = STOCK_CONFIG_DELETE_FIELD,
        timestamp: bool = False,
        assignment: str = "",
        instance_enabled: bool | None = None,
        operation_excluded: bool = False,
    ) -> Path:
        stock_dir = self.root / "stocks" / f"{code}_{name}"
        config: dict[str, object] = {
            "assigned_routine_instance_id": assignment,
            "operation_excluded": operation_excluded,
            "operation_mode": "CONTINUOUS",
            "updated_at": "CONFIG-STABLE",
        }
        if config_legacy is not STOCK_CONFIG_DELETE_FIELD:
            config["real_trade_enabled"] = config_legacy
        if timestamp:
            config["real_trade_policy_updated_at"] = "LEGACY-TIMESTAMP"
        state: dict[str, object] = {
            "status": "STOPPED",
            "holding_qty": 0,
            "updated_at": "STATE-STABLE",
            "signal_probe_only": False,
        }
        if state_legacy is not STOCK_CONFIG_DELETE_FIELD:
            state["real_trade_enabled"] = state_legacy
        _write_json(stock_dir / "config.json", config)
        _write_json(stock_dir / "state.json", state)
        _write_json(stock_dir / "orders.json", {"orders": []})
        if assignment:
            _write_json(
                self.root / "routine_instances" / assignment / "instance.json",
                {"instance_id": assignment, "enabled": instance_enabled},
            )
        return stock_dir

    def _cleanup(self) -> RealTradeRetirementCleanup:
        return RealTradeRetirementCleanup(
            self.root,
            expectation=self.expectation,
            application_active_check=lambda: False,
        )

    def test_preview_apply_readback_and_idempotent_second_run(self) -> None:
        cleanup = self._cleanup()
        preview = cleanup.preview()
        self.assertTrue(preview["ok"])
        self.assertEqual(1, preview["target_config_count"])
        self.assertEqual(1, preview["target_state_count"])

        result = cleanup.apply(expected_preview_id=str(preview["preview_id"]))
        self.assertTrue(result["ok"])
        config = json.loads((self.stock_a / "config.json").read_text(encoding="utf-8"))
        state = json.loads((self.stock_a / "state.json").read_text(encoding="utf-8"))
        self.assertNotIn("real_trade_enabled", config)
        self.assertNotIn("real_trade_policy_updated_at", config)
        self.assertNotIn("real_trade_enabled", state)
        self.assertEqual("CONFIG-STABLE", config["updated_at"])
        self.assertEqual("STATE-STABLE", state["updated_at"])
        self.assertEqual("instance-a", config["assigned_routine_instance_id"])
        self.assertFalse(config["operation_excluded"])

        second_preview = cleanup.preview()
        second = cleanup.apply(expected_preview_id=str(second_preview["preview_id"]))
        self.assertTrue(second["ok"])
        self.assertEqual("CLEANUP_ALREADY_COMPLETE", second["reason_code"])

    def test_preview_hash_drift_blocks_before_cleanup(self) -> None:
        cleanup = self._cleanup()
        preview = cleanup.preview()
        config = json.loads((self.stock_a / "config.json").read_text(encoding="utf-8"))
        config["unrelated"] = "changed"
        _write_json(self.stock_a / "config.json", config)

        result = cleanup.apply(expected_preview_id=str(preview["preview_id"]))

        self.assertFalse(result["ok"])
        self.assertEqual("PREVIEW_STALE", result["reason_code"])
        self.assertIn(
            "real_trade_enabled",
            json.loads((self.stock_a / "config.json").read_text(encoding="utf-8")),
        )

    def test_false_and_timestamp_only_fixtures_are_explicitly_deletable(self) -> None:
        false_root = self.root / "false-fixture"
        self.root = false_root
        self._write_runtime()
        stock = self._write_stock(
            "111111",
            "거짓값",
            config_legacy=False,
            state_legacy=False,
            timestamp=True,
        )
        cleanup = RealTradeRetirementCleanup(
            self.root,
            expectation=R6CleanupInventoryExpectation(
                stock_count=1,
                config_false=1,
                config_timestamp_present=1,
                state_false=1,
            ),
            application_active_check=lambda: False,
        )
        preview = cleanup.preview()
        self.assertTrue(preview["ok"])
        self.assertTrue(cleanup.apply(expected_preview_id=preview["preview_id"])["ok"])
        self.assertEqual(
            CURRENT_SCHEMA,
            inspect_real_trade_schema_compatibility(stock).status,
        )

        timestamp_root = false_root / "timestamp-fixture"
        self.root = timestamp_root
        self._write_runtime()
        timestamp_stock = self._write_stock("222222", "시간", timestamp=True)
        cleanup = RealTradeRetirementCleanup(
            self.root,
            expectation=R6CleanupInventoryExpectation(
                stock_count=1,
                config_timestamp_present=1,
            ),
            application_active_check=lambda: False,
        )
        preview = cleanup.preview()
        self.assertTrue(cleanup.apply(expected_preview_id=preview["preview_id"])["ok"])
        self.assertEqual(
            CURRENT_SCHEMA,
            inspect_real_trade_schema_compatibility(timestamp_stock).status,
        )

    def test_malformed_and_unexpected_false_inventory_fail_closed(self) -> None:
        config_path = self.stock_a / "config.json"
        config_path.write_text("{broken", encoding="utf-8")
        malformed = self._cleanup().preview()
        self.assertFalse(malformed["ok"])
        self.assertEqual("PREVIEW_STALE", malformed["reason_code"])

        config = {
            "assigned_routine_instance_id": "instance-a",
            "operation_excluded": False,
            "operation_mode": "CONTINUOUS",
            "updated_at": "CONFIG-STABLE",
            "real_trade_enabled": False,
            "real_trade_policy_updated_at": "LEGACY-TIMESTAMP",
        }
        _write_json(config_path, config)
        stale = self._cleanup().preview()
        self.assertFalse(stale["ok"])
        self.assertEqual("PREVIEW_STALE", stale["reason_code"])

    def test_partial_resume_uses_current_hash_and_preserves_state(self) -> None:
        repository = StockRepository(self.root)
        config = json.loads((self.stock_a / "config.json").read_text(encoding="utf-8"))
        write = repository.patch_stock_config(
            "005930",
            {
                "real_trade_enabled": STOCK_CONFIG_DELETE_FIELD,
                "real_trade_policy_updated_at": STOCK_CONFIG_DELETE_FIELD,
            },
            name="삼성전자",
            expected_fields={
                "real_trade_enabled": config["real_trade_enabled"],
                "real_trade_policy_updated_at": config["real_trade_policy_updated_at"],
            },
        )
        self.assertTrue(write.ok)
        cleanup = self._cleanup()
        preview = cleanup.preview()
        self.assertTrue(preview["ok"])
        result = cleanup.apply(expected_preview_id=preview["preview_id"])
        self.assertTrue(result["ok"])
        state = json.loads((self.stock_a / "state.json").read_text(encoding="utf-8"))
        self.assertNotIn("real_trade_enabled", state)
        self.assertEqual("STATE-STABLE", state["updated_at"])


class RealTradeRetirementCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stock = self.root / "stocks" / "005930_삼성전자"
        self.config = {"operation_excluded": False}
        self.state = {"status": "RUNNING", "trade_started_at": "2026-09-04"}
        self._save()

    def _save(self) -> None:
        _write_json(self.stock / "config.json", self.config)
        _write_json(self.stock / "state.json", self.state)

    def _inspect(self) -> LegacySchemaCompatibilityResult:
        return inspect_real_trade_schema_compatibility(
            self.stock,
            expected_stock_code="005930",
            project_root=self.root,
        )

    def test_legacy_value_matrix_and_current_schema(self) -> None:
        self.assertEqual(CURRENT_SCHEMA, self._inspect().status)
        for value in (True, False, "false", "0", "off"):
            with self.subTest(config=value):
                self.config["real_trade_enabled"] = value
                self._save()
                self.assertEqual(MIGRATION_REQUIRED, self._inspect().status)
                self.config.pop("real_trade_enabled")
        self.config["real_trade_policy_updated_at"] = "legacy"
        self._save()
        self.assertEqual(MIGRATION_REQUIRED, self._inspect().status)
        self.config.pop("real_trade_policy_updated_at")
        for value in (True, False):
            with self.subTest(state=value):
                self.state["real_trade_enabled"] = value
                self._save()
                self.assertEqual(MIGRATION_REQUIRED, self._inspect().status)
                self.state.pop("real_trade_enabled")

    def test_malformed_and_guard_legacy_fail_closed(self) -> None:
        (self.stock / "config.json").write_text("{broken", encoding="utf-8")
        self.assertEqual(DATA_INVALID, self._inspect().status)
        self._save()
        _write_json(
            self.root / "runtime" / "real_trade_guard.json",
            {"login": True, "account": "123", "real_trade_enabled": True},
        )
        result = self._inspect()
        self.assertEqual(MIGRATION_REQUIRED, result.status)
        self.assertIn("runtime.real_trade_guard.real_trade_enabled", result.legacy_fields)

    def test_execution_universe_blocks_signal_membership_until_cleanup(self) -> None:
        self.config["real_trade_enabled"] = True
        self._save()
        window = SimpleNamespace(startup_recovery_session_ready=lambda refresh=False: True)
        with patch.object(
            execution_universe,
            "auto_trade_current_session_operation_participant_codes",
            return_value=("005930",),
        ), patch.object(
            execution_universe,
            "auto_trade_setting_trade_started",
            return_value=True,
        ):
            blocked = execution_universe.project_execution_universe(
                window,
                stock_dirs=(self.stock,),
            )
        self.assertFalse(blocked.entries[0].execution_member)
        self.assertIn(MIGRATION_REQUIRED, blocked.entries[0].blockers)

        self.config.pop("real_trade_enabled")
        self._save()
        with patch.object(
            execution_universe,
            "auto_trade_current_session_operation_participant_codes",
            return_value=("005930",),
        ), patch.object(
            execution_universe,
            "auto_trade_setting_trade_started",
            return_value=True,
        ):
            allowed = execution_universe.project_execution_universe(
                window,
                stock_dirs=(self.stock,),
            )
        self.assertTrue(allowed.entries[0].execution_member)

    def test_recovery_and_real_order_gate_return_migration_required(self) -> None:
        legacy = LegacySchemaCompatibilityResult(
            MIGRATION_REQUIRED,
            MIGRATION_REQUIRED,
            "005930",
            str(self.stock),
            ("config.real_trade_enabled",),
        )
        owner = SimpleNamespace(kiwoom_api=None)
        with patch.object(
            gui_windows,
            "inspect_stock_code_real_trade_schema",
            return_value=legacy,
        ):
            decision = gui_windows.MainWindow.production_recovery_gate_for_stock(
                owner,
                "005930",
                caller_name="TEST",
            )
        self.assertIsInstance(decision, RecoveryGateDecision)
        self.assertFalse(decision.allowed)
        self.assertEqual(MIGRATION_REQUIRED, decision.reason_code)

        boundary = AutoTradeOrderExecutionBoundary.__new__(
            AutoTradeOrderExecutionBoundary
        )
        boundary._context = SimpleNamespace(
            production_recovery_gate_for_stock=lambda _code, _caller: decision
        )
        reasons = boundary.production_recovery_block_reasons_for_order(
            {
                "execution_request": {
                    "request_preview": {
                        "order_action": "NEW",
                        "code": "005930",
                    }
                }
            },
            caller_name="TEST_REAL_ORDER_RESUME",
        )
        self.assertEqual([MIGRATION_REQUIRED], reasons)


if __name__ == "__main__":
    unittest.main()
