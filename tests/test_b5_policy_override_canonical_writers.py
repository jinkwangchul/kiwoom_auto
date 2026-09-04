# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import gui_auto_trade_setting_window as setting_window
from runtime_io import read_json_dict
from stock_repository import (
    STOCK_CONFIG_EXPECTED_MISSING,
    STOCK_CONFIG_WRITE_ATOMIC_WRITE_FAILED,
    STOCK_CONFIG_WRITE_FIELD_CONFLICT,
    STOCK_CONFIG_WRITE_INVALID_PATCH,
    STOCK_CONFIG_WRITE_NO_CHANGE,
    STOCK_CONFIG_WRITE_READBACK_FAILED,
    StockConfigWriteResult,
    StockRepository,
)


class _Value:
    def __init__(self, value: object) -> None:
        self.value = value

    def isChecked(self) -> bool:
        return bool(self.value)

    def toPlainText(self) -> str:
        return str(self.value)


class _PolicyDialogSurface:
    OVERRIDE_KEYS = setting_window.StockPolicyOverrideDialog.OVERRIDE_KEYS

    def __init__(
        self,
        stock_dir: Path,
        opening_config: dict[str, object],
        *,
        enabled: bool,
        memo: str,
    ) -> None:
        self.stock_dir = stock_dir
        self.config_path = stock_dir / "config.json"
        self.code = "005930"
        self.name = "삼성전자"
        self.config = deepcopy(opening_config)
        self._policy_override_opening_config = deepcopy(opening_config)
        self.use_override = _Value(enabled)
        self.memo = _Value(memo)
        self.accepted = False

    def write_config(self, patch, *, expected_fields):
        return setting_window.StockPolicyOverrideDialog.write_config(
            self,
            patch,
            expected_fields=expected_fields,
        )

    def _append_override_changed(self, before, after):
        return setting_window.StockPolicyOverrideDialog._append_override_changed(
            self,
            before,
            after,
        )

    def accept(self) -> None:
        self.accepted = True


class B5PolicyOverrideCanonicalWritersTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.stock_dir = self.root / "stocks" / "005930_삼성전자"
        self.stock_dir.mkdir(parents=True)
        self.config_path = self.stock_dir / "config.json"
        self.initial = {
            "policy_override_enabled": False,
            "policy_override_memo": "기존 메모",
            "trade_amount_type": "AMOUNT",
            "buy_amount": 20_000,
            "buy_qty": 3,
            "buy_limit_enabled": True,
            "buy_limit_amount": 100_000,
            "buy_limit_source": "MANUAL",
            "operation_mode": "SCHEDULED",
            "start_time": "09:00:00",
            "end_buy_time": "13:30:00",
            "operation_excluded": False,
            "routine": "RoutineA",
            "assigned_routine_instance_id": "instance-a",
            "routine_assignment_history": [{"instance_id": "instance-a"}],
        }
        self._write(self.initial)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write(self, config: dict[str, object]) -> None:
        self.config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _dialog(self, *, enabled: bool, memo: str) -> _PolicyDialogSurface:
        return _PolicyDialogSurface(
            self.stock_dir,
            deepcopy(self.initial),
            enabled=enabled,
            memo=memo,
        )

    def _save(self, dialog: _PolicyDialogSurface) -> None:
        with (
            patch.object(setting_window, "now_text", return_value="2026-08-29 12:00:00"),
            patch.object(setting_window, "append_stock_log"),
            patch.object(setting_window, "append_changelog"),
            patch.object(setting_window, "append_production_event"),
            patch.object(setting_window.QMessageBox, "information"),
            patch.object(setting_window.QMessageBox, "critical"),
        ):
            setting_window.StockPolicyOverrideDialog.save_override(dialog)

    def _reset(self, dialog: _PolicyDialogSurface) -> None:
        with (
            patch.object(setting_window, "now_text", return_value="2026-08-29 12:00:00"),
            patch.object(setting_window, "append_stock_log"),
            patch.object(setting_window, "append_changelog"),
            patch.object(setting_window, "append_production_event"),
            patch.object(setting_window.QMessageBox, "information"),
            patch.object(setting_window.QMessageBox, "critical"),
        ):
            setting_window.StockPolicyOverrideDialog.reset_all_to_global(dialog)

    def test_dialog_save_patches_only_edited_field_and_preserves_b4_domains(self) -> None:
        dialog = self._dialog(enabled=True, memo="기존 메모")
        concurrent = deepcopy(self.initial)
        concurrent.update(
            {
                "buy_amount": 70_000,
                "buy_limit_amount": 900_000,
                "operation_mode": "CONTINUOUS",
                "operation_excluded": True,
                "assigned_routine_instance_id": "instance-b",
                "routine_assignment_history": [{"instance_id": "instance-b"}],
            }
        )
        self._write(concurrent)
        original_patch = StockRepository.patch_stock_config

        with patch.object(
            setting_window.StockRepository,
            "patch_stock_config",
            autospec=True,
            side_effect=original_patch,
        ) as canonical_patch:
            self._save(dialog)

        saved = read_json_dict(self.config_path)
        self.assertTrue(dialog.accepted)
        self.assertTrue(saved["policy_override_enabled"])
        self.assertEqual(70_000, saved["buy_amount"])
        self.assertEqual(900_000, saved["buy_limit_amount"])
        self.assertEqual("CONTINUOUS", saved["operation_mode"])
        self.assertTrue(saved["operation_excluded"])
        self.assertNotIn("real_trade_enabled", saved)
        self.assertEqual("instance-b", saved["assigned_routine_instance_id"])
        self.assertEqual([{"instance_id": "instance-b"}], saved["routine_assignment_history"])
        self.assertEqual(1, canonical_patch.call_count)
        self.assertEqual(
            {"policy_override_enabled": False},
            canonical_patch.call_args.kwargs["expected_fields"],
        )
        self.assertNotIn("buy_amount", canonical_patch.call_args.args[2])

    def test_same_field_conflict_fails_closed(self) -> None:
        dialog = self._dialog(enabled=True, memo="기존 메모")
        concurrent = deepcopy(self.initial)
        concurrent["policy_override_enabled"] = True
        self._write(concurrent)

        self._save(dialog)

        self.assertFalse(dialog.accepted)
        self.assertEqual(
            STOCK_CONFIG_WRITE_FIELD_CONFLICT,
            dialog._last_config_write_result.reason_code,
        )
        self.assertEqual(concurrent, read_json_dict(self.config_path))

    def test_missing_field_creation_conflicts(self) -> None:
        opening = deepcopy(self.initial)
        opening.pop("policy_override_enabled")
        self._write(opening)
        dialog = _PolicyDialogSurface(
            self.stock_dir,
            opening,
            enabled=True,
            memo="기존 메모",
        )
        concurrent = deepcopy(opening)
        concurrent["policy_override_enabled"] = True
        self._write(concurrent)

        self._save(dialog)

        self.assertFalse(dialog.accepted)
        self.assertEqual(
            STOCK_CONFIG_WRITE_FIELD_CONFLICT,
            dialog._last_config_write_result.reason_code,
        )
        self.assertEqual(concurrent, read_json_dict(self.config_path))

    def test_unchanged_confirm_skips_canonical_write(self) -> None:
        dialog = self._dialog(enabled=False, memo="기존 메모")
        before = self.config_path.read_bytes()
        before_mtime = self.config_path.stat().st_mtime_ns

        with patch.object(setting_window.StockRepository, "patch_stock_config") as writer:
            self._save(dialog)

        self.assertTrue(dialog.accepted)
        self.assertFalse(dialog._last_config_write_result.changed)
        self.assertEqual(STOCK_CONFIG_WRITE_NO_CHANGE, dialog._last_config_write_result.reason_code)
        writer.assert_not_called()
        self.assertEqual(before, self.config_path.read_bytes())
        self.assertEqual(before_mtime, self.config_path.stat().st_mtime_ns)

    def test_reset_is_one_atomic_patch_and_preserves_unrelated_fields(self) -> None:
        opening = deepcopy(self.initial)
        opening.update(
            {
                "operation_policy_override": {"enabled": True},
                "manual_operation_override": {"extra1": True},
                "scheduled_operation_override": {"start_time": "10:00:00"},
                "auto_close_override": {"enabled": True},
                "early_close_override": {"method": "현재가"},
                "liquidation_override": {"method": "시장가"},
            }
        )
        self._write(opening)
        dialog = _PolicyDialogSurface(
            self.stock_dir,
            opening,
            enabled=True,
            memo="기존 메모",
        )
        original_patch = StockRepository.patch_stock_config

        with patch.object(
            setting_window.StockRepository,
            "patch_stock_config",
            autospec=True,
            side_effect=original_patch,
        ) as canonical_patch:
            self._reset(dialog)

        saved = read_json_dict(self.config_path)
        self.assertTrue(dialog.accepted)
        self.assertEqual(1, canonical_patch.call_count)
        self.assertFalse(saved["policy_override_enabled"])
        for key in setting_window.POLICY_OVERRIDE_VALUE_FIELDS[1:] + (
            "policy_override_memo",
        ):
            self.assertNotIn(key, saved)
        self.assertEqual("2026-08-29 12:00:00", saved["policy_override_reset_at"])
        self.assertEqual(20_000, saved["buy_amount"])
        self.assertEqual(100_000, saved["buy_limit_amount"])
        self.assertEqual("instance-a", saved["assigned_routine_instance_id"])

    def test_reset_same_field_conflict_changes_nothing(self) -> None:
        opening = deepcopy(self.initial)
        opening["manual_operation_override"] = {"extra1": True}
        self._write(opening)
        dialog = _PolicyDialogSurface(
            self.stock_dir,
            opening,
            enabled=False,
            memo="기존 메모",
        )
        concurrent = deepcopy(opening)
        concurrent["manual_operation_override"] = {"extra1": False}
        self._write(concurrent)

        self._reset(dialog)

        self.assertFalse(dialog.accepted)
        self.assertEqual(
            STOCK_CONFIG_WRITE_FIELD_CONFLICT,
            dialog._last_config_write_result.reason_code,
        )
        self.assertEqual(concurrent, read_json_dict(self.config_path))

    def test_reset_without_override_is_no_change(self) -> None:
        opening = {
            key: value
            for key, value in self.initial.items()
            if key not in setting_window.POLICY_OVERRIDE_WRITABLE_FIELDS
        }
        opening["policy_override_enabled"] = False
        self._write(opening)
        dialog = _PolicyDialogSurface(
            self.stock_dir,
            opening,
            enabled=False,
            memo="",
        )
        before = self.config_path.read_bytes()

        with patch.object(setting_window.StockRepository, "patch_stock_config") as writer:
            self._reset(dialog)

        self.assertTrue(dialog.accepted)
        self.assertFalse(dialog._last_config_write_result.changed)
        self.assertEqual(STOCK_CONFIG_WRITE_NO_CHANGE, dialog._last_config_write_result.reason_code)
        writer.assert_not_called()
        self.assertEqual(before, self.config_path.read_bytes())

    def test_atomic_write_failure_preserves_entire_config(self) -> None:
        before = self.config_path.read_bytes()
        with patch.object(
            StockRepository,
            "_atomic_write_stock_config",
            side_effect=OSError("write failed"),
        ):
            result = setting_window._patch_stock_policy_override_config(
                self.stock_dir,
                "005930",
                {"policy_override_enabled": True},
                expected_fields={"policy_override_enabled": False},
            )

        self.assertFalse(result.ok)
        self.assertEqual(STOCK_CONFIG_WRITE_ATOMIC_WRITE_FAILED, result.reason_code)
        self.assertEqual(before, self.config_path.read_bytes())

    def test_readback_failure_does_not_accept_dialog(self) -> None:
        dialog = self._dialog(enabled=True, memo="기존 메모")
        failure = StockConfigWriteResult(
            ok=False,
            changed=True,
            field_keys=("policy_override_enabled",),
            conflict_detected=False,
            read_back_verified=False,
            reason_code=STOCK_CONFIG_WRITE_READBACK_FAILED,
        )
        with patch.object(
            setting_window,
            "_patch_stock_policy_override_config",
            return_value=failure,
        ):
            self._save(dialog)

        self.assertFalse(dialog.accepted)
        self.assertEqual(STOCK_CONFIG_WRITE_READBACK_FAILED, dialog._last_config_write_result.reason_code)

    def test_manual_override_set_and_reset_use_canonical_patch(self) -> None:
        continuous = deepcopy(self.initial)
        continuous["operation_mode"] = "CONTINUOUS"
        self._write(continuous)
        host = SimpleNamespace(
            selected_stock_infos=lambda: [(self.stock_dir, "005930", "삼성전자")],
            statusBarMessage=Mock(),
            refresh_all=Mock(),
        )
        original_patch = StockRepository.patch_stock_config
        with (
            patch.object(setting_window, "now_text", return_value="2026-08-29 12:00:00"),
            patch.object(setting_window, "append_stock_log"),
            patch.object(setting_window, "append_changelog"),
            patch.object(setting_window.QMessageBox, "critical"),
            patch.object(setting_window.QMessageBox, "information"),
            patch.object(
                setting_window.StockRepository,
                "patch_stock_config",
                autospec=True,
                side_effect=original_patch,
            ) as canonical_patch,
        ):
            setting_window.AutoTradeSettingWindow.toggle_selected_manual_override_flag(
                host,
                "extra1",
                "장전 ATS",
            )
            setting_window.AutoTradeSettingWindow.reset_selected_manual_override(host)

        saved = read_json_dict(self.config_path)
        self.assertEqual(2, canonical_patch.call_count)
        self.assertNotIn("manual_operation_override", saved)
        self.assertTrue(saved["policy_override_enabled"])
        self.assertEqual(20_000, saved["buy_amount"])
        self.assertEqual("instance-a", saved["assigned_routine_instance_id"])

    def test_policy_writer_rejects_non_owned_field(self) -> None:
        result = setting_window._patch_stock_policy_override_config(
            self.stock_dir,
            "005930",
            {"buy_amount": 70_000},
            expected_fields={"buy_amount": 20_000},
        )

        self.assertFalse(result.ok)
        self.assertEqual(STOCK_CONFIG_WRITE_INVALID_PATCH, result.reason_code)
        self.assertEqual(self.initial, read_json_dict(self.config_path))

    def test_expected_missing_helper_uses_canonical_sentinel(self) -> None:
        expected = setting_window._stock_policy_override_expected_fields(
            {},
            ("policy_override_enabled",),
        )
        self.assertIs(
            STOCK_CONFIG_EXPECTED_MISSING,
            expected["policy_override_enabled"],
        )


if __name__ == "__main__":
    unittest.main()
