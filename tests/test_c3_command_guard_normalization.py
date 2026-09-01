# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import gui_auto_trade_setting_window as setting_window_module
import gui_auto_trade_status_ops as status_ops
import gui_main_stock_context_menu as main_context_module
import gui_routine_service as routine_service
from tests.participant_owner_fixture import participant_owner


def _write_stock(
    root: Path,
    code: str = "005930",
    *,
    config: dict[str, object] | None = None,
    state: dict[str, object] | None = None,
) -> Path:
    stock_dir = root / "stocks" / f"{code}_Test"
    stock_dir.mkdir(parents=True)
    stock_config = {
        "operation_mode": "CONTINUOUS",
        "operation_excluded": False,
        "real_trade_enabled": True,
        "routine": "RoutineA",
        "routine_instance_name": "RoutineA",
        "assigned_routine_instance_id": "instance-a",
        "buy_amount": 100_000,
        "buy_limit_amount": 1_000_000,
        "policy_override": {"owner": "fixture"},
    }
    stock_config.update(config or {})
    stock_state = {"status": "STOPPED", "trade_enabled": False}
    stock_state.update(state or {})
    (stock_dir / "config.json").write_text(
        json.dumps(stock_config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (stock_dir / "state.json").write_text(
        json.dumps(stock_state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return stock_dir


def _window(*participants: str) -> SimpleNamespace:
    return SimpleNamespace(
        _main_monitoring_auto_trade_operation_host=participant_owner(participants)
    )


class C3ExclusionGuardTests(unittest.TestCase):
    def test_review_exclusion_and_release_call_no_writer(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = _write_stock(
                Path(temp),
                config={"operation_excluded": True},
                state={"status": "REVIEW_REQUIRED", "review_required": True},
            )
            target = (stock_dir, "005930", "Test")
            before = (stock_dir / "config.json").read_bytes()
            with patch.object(
                status_ops,
                "_patch_auto_trade_stock_operation_excluded",
            ) as writer:
                for requested in (False, True):
                    with self.subTest(requested=requested):
                        result = status_ops.execute_auto_trade_stock_operation_exclusion(
                            _window(),
                            target,
                            requested,
                        )
                        self.assertFalse(result.ok)
                        self.assertFalse(result.allowed)
                        self.assertEqual("REVIEW_REQUIRED", result.reason_code)

            writer.assert_not_called()
            self.assertEqual(before, (stock_dir / "config.json").read_bytes())

    def test_current_participant_exclusion_calls_no_writer(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = _write_stock(Path(temp))
            target = (stock_dir, "005930", "Test")
            before = (stock_dir / "config.json").read_bytes()
            with patch.object(
                status_ops,
                "_patch_auto_trade_stock_operation_excluded",
            ) as writer:
                result = status_ops.execute_auto_trade_stock_operation_exclusion(
                    _window("005930"),
                    target,
                    True,
                )

            self.assertFalse(result.ok)
            self.assertEqual("CURRENTLY_RUNNING", result.reason_code)
            writer.assert_not_called()
            self.assertEqual(before, (stock_dir / "config.json").read_bytes())

    def test_stale_raw_running_without_participant_does_not_block_exclusion(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = _write_stock(
                Path(temp),
                state={
                    "status": "RUNNING",
                    "trade_enabled": True,
                    "trade_started_at": "2026-08-29 09:00:00",
                },
            )
            target = (stock_dir, "005930", "Test")
            result = status_ops.execute_auto_trade_stock_operation_exclusion(
                _window(),
                target,
                True,
            )

            self.assertTrue(result.ok)
            self.assertTrue(result.changed)
            saved = json.loads(
                (stock_dir / "config.json").read_text(encoding="utf-8")
            )
            self.assertTrue(saved["operation_excluded"])
            self.assertEqual(100_000, saved["buy_amount"])
            self.assertEqual({"owner": "fixture"}, saved["policy_override"])

    def test_exclusion_no_change_calls_no_writer(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = _write_stock(Path(temp))
            target = (stock_dir, "005930", "Test")
            before = (stock_dir / "config.json").read_bytes()
            with patch.object(
                status_ops,
                "_patch_auto_trade_stock_operation_excluded",
            ) as writer:
                result = status_ops.execute_auto_trade_stock_operation_exclusion(
                    _window(),
                    target,
                    False,
                )

            self.assertTrue(result.ok)
            self.assertFalse(result.changed)
            self.assertEqual("NOT_EXCLUDED", result.reason_code)
            writer.assert_not_called()
            self.assertEqual(before, (stock_dir / "config.json").read_bytes())


class C3TradePermissionGuardTests(unittest.TestCase):
    def test_stale_raw_running_without_participant_does_not_block_change(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = _write_stock(
                Path(temp),
                state={
                    "status": "RUNNING",
                    "trade_enabled": True,
                    "trade_started_at": "2026-08-29 09:00:00",
                },
            )
            result = routine_service.set_stock_real_trade_enabled(
                _window(),
                stock_dir,
                "005930",
                "Test",
                False,
            )

            self.assertTrue(result["ok"], result)
            self.assertTrue(result["changed"])
            saved = json.loads(
                (stock_dir / "config.json").read_text(encoding="utf-8")
            )
            self.assertFalse(saved["real_trade_enabled"])
            self.assertEqual(1_000_000, saved["buy_limit_amount"])
            self.assertEqual({"owner": "fixture"}, saved["policy_override"])

    def test_current_participant_change_calls_no_writer(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = _write_stock(Path(temp))
            before = (stock_dir / "config.json").read_bytes()
            with patch.object(routine_service, "_patch_real_trade_enabled") as writer:
                result = routine_service.set_stock_real_trade_enabled(
                    _window("005930"),
                    stock_dir,
                    "005930",
                    "Test",
                    False,
                )

            self.assertFalse(result["ok"])
            self.assertEqual("CURRENT_SESSION_PARTICIPANT", result["reason_code"])
            writer.assert_not_called()
            self.assertEqual(before, (stock_dir / "config.json").read_bytes())

    def test_review_change_calls_no_writer(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = _write_stock(
                Path(temp),
                state={"status": "REVIEW_REQUIRED", "review_required": True},
            )
            before = (stock_dir / "config.json").read_bytes()
            with patch.object(routine_service, "_patch_real_trade_enabled") as writer:
                result = routine_service.set_stock_real_trade_enabled(
                    _window(),
                    stock_dir,
                    "005930",
                    "Test",
                    False,
                )

            self.assertFalse(result["ok"])
            self.assertEqual("REVIEW_REQUIRED", result["reason_code"])
            writer.assert_not_called()
            self.assertEqual(before, (stock_dir / "config.json").read_bytes())

    def test_real_trade_no_change_calls_no_writer(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = _write_stock(
                Path(temp),
                config={"real_trade_enabled": False},
            )
            before = (stock_dir / "config.json").read_bytes()
            with patch.object(routine_service, "_patch_real_trade_enabled") as writer:
                result = routine_service.set_stock_real_trade_enabled(
                    _window(),
                    stock_dir,
                    "005930",
                    "Test",
                    False,
                )

            self.assertTrue(result["ok"])
            self.assertFalse(result["changed"])
            self.assertEqual("REALTRADE_STATE_UNCHANGED", result["reason_code"])
            writer.assert_not_called()
            self.assertEqual(before, (stock_dir / "config.json").read_bytes())

    def test_batch_preserves_partial_result_details(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            normal = _write_stock(root, "005930")
            review = _write_stock(
                root,
                "000660",
                state={"status": "REVIEW_REQUIRED"},
            )
            result = routine_service.execute_selected_stock_real_trade_command(
                _window(),
                [
                    (normal, "005930", "Normal"),
                    (review, "000660", "Review"),
                ],
                False,
            )

            self.assertFalse(result["ok"])
            self.assertEqual(1, result["changed"])
            self.assertEqual(1, result["blocked"])
            self.assertEqual("PARTIAL_BLOCKED", result["reason_code"])
            self.assertEqual(2, len(result["results"]))

    def test_batch_preserves_single_canonical_block_reason(self) -> None:
        with TemporaryDirectory() as temp:
            review = _write_stock(
                Path(temp),
                state={"status": "REVIEW_REQUIRED"},
            )
            result = routine_service.execute_selected_stock_real_trade_command(
                _window(),
                [(review, "005930", "Review")],
                False,
            )

            self.assertFalse(result["ok"])
            self.assertEqual(0, result["changed"])
            self.assertEqual(1, result["blocked"])
            self.assertEqual("REVIEW_REQUIRED", result["reason_code"])

    def test_availability_queries_are_read_only(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = _write_stock(Path(temp))
            config_path = stock_dir / "config.json"
            state_path = stock_dir / "state.json"
            before = (config_path.read_bytes(), state_path.read_bytes())
            target = (stock_dir, "005930", "Test")

            exclusion = status_ops.inspect_auto_trade_operation_exclusion_availability(
                _window(),
                target,
                True,
            )
            permission = (
                routine_service.inspect_stock_real_trade_transition_availability(
                    _window(),
                    stock_dir,
                    "005930",
                    False,
                )
            )

            self.assertTrue(exclusion.allowed)
            self.assertTrue(permission.allowed)
            self.assertEqual(before, (config_path.read_bytes(), state_path.read_bytes()))

    def test_main_and_settings_import_the_same_semantic_commands(self) -> None:
        self.assertIs(
            setting_window_module.execute_selected_stock_real_trade_command,
            routine_service.execute_selected_stock_real_trade_command,
        )
        self.assertIs(
            main_context_module.execute_selected_stock_real_trade_command,
            routine_service.execute_selected_stock_real_trade_command,
        )
        self.assertIs(
            setting_window_module.auto_trade_set_stock_operation_exclusion,
            status_ops.auto_trade_set_stock_operation_exclusion,
        )
        self.assertIs(
            main_context_module.auto_trade_set_selected_stock_operation_exclusions,
            status_ops.auto_trade_set_selected_stock_operation_exclusions,
        )
        self.assertIs(
            main_context_module.auto_trade_clear_selected_stock_operation_exclusions,
            status_ops.auto_trade_clear_selected_stock_operation_exclusions,
        )

    def test_main_and_settings_trade_availability_are_equal(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = _write_stock(
                Path(temp),
                state={"status": "RUNNING", "trade_enabled": True},
            )
            selected = [(stock_dir, "005930", "Test")]
            main_window = SimpleNamespace(
                routine_table=object(),
                _main_monitoring_auto_trade_operation_host=participant_owner({"005930"}),
            )
            adapter = main_context_module.MainMonitoringStockOperationAdapter(
                main_window,
                [
                    main_context_module.MainMonitoringStockTarget(
                        stock_dir=stock_dir,
                        code="005930",
                        name="Test",
                        routine_instance_id="instance-a",
                    )
                ],
            )
            settings_window = _window("005930")

            main_allowed = main_context_module.selected_stock_trade_permission_available(
                adapter,
                selected,
            )
            settings_allowed = (
                setting_window_module.AutoTradeSettingWindow.selected_trade_permission_available(
                    settings_window,
                    selected,
                )
            )

            self.assertFalse(main_allowed)
            self.assertEqual(main_allowed, settings_allowed)


if __name__ == "__main__":
    unittest.main()
