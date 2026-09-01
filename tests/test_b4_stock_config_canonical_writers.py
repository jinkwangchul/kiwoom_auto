# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import gui_auto_trade_status_ops as status_ops
import gui_routine_service as routine_service
from runtime_io import read_json_dict
from stock_repository import STOCK_CONFIG_WRITE_FIELD_CONFLICT, StockRepository
from tests.participant_owner_fixture import attach_participant_owner


class B4StockConfigCanonicalWritersTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.stock_dir = self.root / "stocks" / "005930_삼성전자"
        self.stock_dir.mkdir(parents=True)
        self.config_path = self.stock_dir / "config.json"
        self.state_path = self.stock_dir / "state.json"
        self.config = {
            "operation_mode": "SCHEDULED",
            "start_time": "09:00:00",
            "trade_start_time": "09:00:00",
            "end_buy_time": "13:30:00",
            "buy_end_time": "13:30:00",
            "operation_excluded": False,
            "real_trade_enabled": True,
            "routine": "RoutineA",
            "routine_instance_name": "RoutineA",
            "assigned_routine_instance_id": "instance-a",
            "trade_amount_type": "AMOUNT",
            "buy_amount": 100_000,
            "buy_limit_amount": 1_000_000,
            "policy_override": {"owner": "fixture"},
        }
        self._write_config(self.config)
        self._write_state({"status": "STOPPED", "trade_enabled": False})
        self.window = SimpleNamespace(
            running_registered_operation_targets=Mock(return_value=[]),
            statusBarMessage=Mock(),
            refresh_all=Mock(),
        )
        attach_participant_owner(self.window)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_config(self, config: dict[str, object]) -> None:
        self.config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _write_state(self, state: dict[str, object]) -> None:
        self.state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _change_operation_mode(
        self,
        mode: str,
        updates: dict[str, object] | None = None,
    ) -> bool:
        with (
            patch.object(
                status_ops,
                "current_datetime",
                return_value=datetime(2026, 8, 29, 12, 0),
            ),
            patch.object(status_ops, "append_stock_log"),
            patch.object(status_ops, "append_production_event"),
        ):
            return status_ops.auto_trade_update_stock_operation_mode(
                self.window,
                self.stock_dir,
                "005930",
                "삼성전자",
                mode,
                updates,
            )

    def _set_excluded(self, excluded: bool) -> bool:
        with (
            patch.object(status_ops, "append_stock_log"),
            patch.object(status_ops, "append_changelog"),
            patch.object(status_ops, "append_production_event"),
            patch.object(status_ops, "show_toast"),
            patch.object(status_ops.QMessageBox, "critical"),
        ):
            return status_ops.auto_trade_set_stock_operation_exclusion(
                self.window,
                (self.stock_dir, "005930", "삼성전자"),
                excluded,
            )

    def test_operation_mode_and_time_use_canonical_partial_patch(self) -> None:
        original_patch = StockRepository.patch_stock_config
        with patch.object(
            status_ops.StockRepository,
            "patch_stock_config",
            autospec=True,
            side_effect=original_patch,
        ) as canonical_patch:
            changed = self._change_operation_mode(
                "SCHEDULED",
                {"start_time": "09:10", "end_buy_time": "13:20"},
            )

        self.assertTrue(changed)
        written_fields = set(canonical_patch.call_args.args[2])
        self.assertEqual(
            {
                "operation_mode",
                "start_time",
                "trade_start_time",
                "end_buy_time",
                "buy_end_time",
                "operation_mode_updated_at",
            },
            written_fields,
        )
        saved = read_json_dict(self.config_path)
        self.assertEqual("09:10:00", saved["start_time"])
        self.assertEqual("13:20:00", saved["end_buy_time"])
        self.assertEqual(100_000, saved["buy_amount"])
        self.assertEqual({"owner": "fixture"}, saved["policy_override"])

    def test_operation_mode_no_change_skips_repository(self) -> None:
        before = self.config_path.read_bytes()
        with patch.object(
            status_ops.StockRepository,
            "patch_stock_config",
        ) as canonical_patch:
            changed = self._change_operation_mode("SCHEDULED")

        self.assertTrue(changed)
        canonical_patch.assert_not_called()
        self.assertEqual(before, self.config_path.read_bytes())

    def test_operation_mode_same_field_conflict_fails_closed(self) -> None:
        original_patch = StockRepository.patch_stock_config

        def conflict(repository, code, patch_values, **kwargs):
            concurrent = read_json_dict(self.config_path)
            concurrent["operation_mode"] = "CONTINUOUS"
            concurrent["conflict_owner"] = "other-writer"
            self._write_config(concurrent)
            return original_patch(repository, code, patch_values, **kwargs)

        with patch.object(
            status_ops.StockRepository,
            "patch_stock_config",
            autospec=True,
            side_effect=conflict,
        ):
            changed = self._change_operation_mode("CONTINUOUS")

        self.assertFalse(changed)
        saved = read_json_dict(self.config_path)
        self.assertEqual("CONTINUOUS", saved["operation_mode"])
        self.assertEqual("other-writer", saved["conflict_owner"])
        self.assertNotIn("operation_mode_updated_at", saved)

    def test_operation_mode_merges_unrelated_concurrent_budget_change(self) -> None:
        original_patch = StockRepository.patch_stock_config

        def concurrent_budget(repository, code, patch_values, **kwargs):
            concurrent = read_json_dict(self.config_path)
            concurrent["buy_amount"] = 250_000
            self._write_config(concurrent)
            return original_patch(repository, code, patch_values, **kwargs)

        with patch.object(
            status_ops.StockRepository,
            "patch_stock_config",
            autospec=True,
            side_effect=concurrent_budget,
        ):
            changed = self._change_operation_mode("CONTINUOUS")

        self.assertTrue(changed)
        saved = read_json_dict(self.config_path)
        self.assertEqual("CONTINUOUS", saved["operation_mode"])
        self.assertEqual(250_000, saved["buy_amount"])

    def test_exclusion_uses_canonical_patch_and_preserves_unrelated_fields(self) -> None:
        original_patch = StockRepository.patch_stock_config
        with patch.object(
            status_ops.StockRepository,
            "patch_stock_config",
            autospec=True,
            side_effect=original_patch,
        ) as canonical_patch:
            changed = self._set_excluded(True)

        self.assertTrue(changed)
        self.assertEqual(
            {"operation_excluded", "updated_at"},
            set(canonical_patch.call_args.args[2]),
        )
        saved = read_json_dict(self.config_path)
        self.assertTrue(saved["operation_excluded"])
        self.assertEqual(1_000_000, saved["buy_limit_amount"])
        self.assertTrue(saved["real_trade_enabled"])

    def test_exclusion_same_field_conflict_does_not_overwrite(self) -> None:
        original_patch = StockRepository.patch_stock_config

        def conflict(repository, code, patch_values, **kwargs):
            concurrent = read_json_dict(self.config_path)
            concurrent["operation_excluded"] = "external"
            self._write_config(concurrent)
            return original_patch(repository, code, patch_values, **kwargs)

        with patch.object(
            status_ops.StockRepository,
            "patch_stock_config",
            autospec=True,
            side_effect=conflict,
        ):
            changed = self._set_excluded(True)

        self.assertFalse(changed)
        self.assertEqual("external", read_json_dict(self.config_path)["operation_excluded"])

    def test_exclusion_merges_unrelated_concurrent_limit_change(self) -> None:
        original_patch = StockRepository.patch_stock_config

        def concurrent_limit(repository, code, patch_values, **kwargs):
            concurrent = read_json_dict(self.config_path)
            concurrent["buy_limit_amount"] = 2_500_000
            self._write_config(concurrent)
            return original_patch(repository, code, patch_values, **kwargs)

        with patch.object(
            status_ops.StockRepository,
            "patch_stock_config",
            autospec=True,
            side_effect=concurrent_limit,
        ):
            changed = self._set_excluded(True)

        self.assertTrue(changed)
        saved = read_json_dict(self.config_path)
        self.assertTrue(saved["operation_excluded"])
        self.assertEqual(2_500_000, saved["buy_limit_amount"])

    def test_exclusion_running_guard_calls_no_writer(self) -> None:
        target = (self.stock_dir, "005930", "삼성전자")
        self.window.running_registered_operation_targets.return_value = [target]
        before = self.config_path.read_bytes()
        with patch.object(status_ops, "_patch_auto_trade_stock_operation_excluded") as writer:
            changed = status_ops.auto_trade_set_stock_operation_exclusion(
                self.window,
                target,
                True,
            )

        self.assertFalse(changed)
        writer.assert_not_called()
        self.assertEqual(before, self.config_path.read_bytes())

    def test_real_trade_uses_canonical_patch_and_no_change_skips_writer(self) -> None:
        original_patch = StockRepository.patch_stock_config
        with patch.object(
            routine_service.StockRepository,
            "patch_stock_config",
            autospec=True,
            side_effect=original_patch,
        ) as canonical_patch:
            changed = routine_service.set_stock_real_trade_enabled(
                self.window,
                self.stock_dir,
                "005930",
                "삼성전자",
                False,
            )

        self.assertTrue(changed["ok"], changed)
        self.assertTrue(changed["changed"])
        self.assertEqual(
            {
                "real_trade_enabled",
                "real_trade_policy_updated_at",
                "updated_at",
            },
            set(canonical_patch.call_args.args[2]),
        )
        saved = read_json_dict(self.config_path)
        self.assertFalse(saved["real_trade_enabled"])
        self.assertEqual(100_000, saved["buy_amount"])

        before = self.config_path.read_bytes()
        with patch.object(
            routine_service.StockRepository,
            "patch_stock_config",
        ) as canonical_patch:
            unchanged = routine_service.set_stock_real_trade_enabled(
                self.window,
                self.stock_dir,
                "005930",
                "삼성전자",
                False,
            )
        self.assertTrue(unchanged["ok"], unchanged)
        self.assertFalse(unchanged["changed"])
        canonical_patch.assert_not_called()
        self.assertEqual(before, self.config_path.read_bytes())

    def test_real_trade_same_field_conflict_fails_closed(self) -> None:
        original_patch = StockRepository.patch_stock_config

        def conflict(repository, code, patch_values, **kwargs):
            concurrent = read_json_dict(self.config_path)
            concurrent["real_trade_enabled"] = None
            concurrent["conflict_owner"] = "other-writer"
            self._write_config(concurrent)
            return original_patch(repository, code, patch_values, **kwargs)

        with patch.object(
            routine_service.StockRepository,
            "patch_stock_config",
            autospec=True,
            side_effect=conflict,
        ):
            result = routine_service.set_stock_real_trade_enabled(
                self.window,
                self.stock_dir,
                "005930",
                "삼성전자",
                False,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(STOCK_CONFIG_WRITE_FIELD_CONFLICT, result["reason"])
        saved = read_json_dict(self.config_path)
        self.assertIsNone(saved["real_trade_enabled"])
        self.assertEqual("other-writer", saved["conflict_owner"])

    def test_real_trade_merges_unrelated_concurrent_budget_change(self) -> None:
        original_patch = StockRepository.patch_stock_config

        def concurrent_budget(repository, code, patch_values, **kwargs):
            concurrent = read_json_dict(self.config_path)
            concurrent["buy_amount"] = 300_000
            self._write_config(concurrent)
            return original_patch(repository, code, patch_values, **kwargs)

        with patch.object(
            routine_service.StockRepository,
            "patch_stock_config",
            autospec=True,
            side_effect=concurrent_budget,
        ):
            result = routine_service.set_stock_real_trade_enabled(
                self.window,
                self.stock_dir,
                "005930",
                "삼성전자",
                False,
            )

        self.assertTrue(result["ok"], result)
        saved = read_json_dict(self.config_path)
        self.assertFalse(saved["real_trade_enabled"])
        self.assertEqual(300_000, saved["buy_amount"])

    def test_real_trade_running_guard_calls_no_writer(self) -> None:
        self._write_state({"status": "RUNNING", "trade_enabled": True})
        attach_participant_owner(self.window, {"005930"})
        before = self.config_path.read_bytes()
        with patch.object(routine_service, "_patch_real_trade_enabled") as writer:
            result = routine_service.set_stock_real_trade_enabled(
                self.window,
                self.stock_dir,
                "005930",
                "삼성전자",
                False,
            )

        self.assertFalse(result["ok"])
        writer.assert_not_called()
        self.assertEqual(before, self.config_path.read_bytes())

    def test_cross_field_sequence_preserves_budget_limit_real_trade_and_override(self) -> None:
        self.assertTrue(self._change_operation_mode("CONTINUOUS"))
        self.assertTrue(self._set_excluded(True))
        result = routine_service.set_stock_real_trade_enabled(
            self.window,
            self.stock_dir,
            "005930",
            "삼성전자",
            False,
        )
        self.assertTrue(result["ok"], result)

        saved = read_json_dict(self.config_path)
        self.assertEqual("CONTINUOUS", saved["operation_mode"])
        self.assertTrue(saved["operation_excluded"])
        self.assertFalse(saved["real_trade_enabled"])
        self.assertEqual(100_000, saved["buy_amount"])
        self.assertEqual(1_000_000, saved["buy_limit_amount"])
        self.assertEqual({"owner": "fixture"}, saved["policy_override"])


if __name__ == "__main__":
    unittest.main()
