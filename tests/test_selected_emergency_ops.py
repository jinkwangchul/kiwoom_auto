# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import gui_main_emergency_ops as emergency_ops
from gui_auto_trade_setting_window import AutoTradeSettingWindow
from runtime_io import read_json_dict


class SelectedEmergencyOpsTest(unittest.TestCase):
    def _target(
        self,
        root: str,
        code: str,
        *,
        status: str = "STOPPED",
    ) -> tuple[Path, str, str]:
        stock_dir = Path(root) / f"{code}_TEST"
        stock_dir.mkdir(parents=True)
        (stock_dir / "config.json").write_text("{}\n", encoding="utf-8")
        (stock_dir / "orders.json").write_text('{"orders": []}\n', encoding="utf-8")
        (stock_dir / "state.json").write_text(
            json.dumps(
                {
                    "status": status,
                    "holding_qty": 0,
                    "trade_enabled": status == "RUNNING",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        return stock_dir, code, "테스트"

    @staticmethod
    def _window() -> SimpleNamespace:
        return SimpleNamespace(
            refresh_all=Mock(),
            statusBarMessage=Mock(),
            production_recovery_stock_is_review_required=lambda _code: False,
            startup_recovery_session_ready=lambda refresh=False: True,
        )

    def test_stop_changes_only_selected_target_and_verifies_saved_state(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            selected = self._target(root, "000001")
            untouched = self._target(root, "000002", status="RUNNING")
            before_untouched = read_json_dict(untouched[0] / "state.json")
            window = self._window()
            with (
                patch.object(emergency_ops, "append_changelog"),
                patch.object(emergency_ops, "append_stock_log"),
                patch.object(emergency_ops, "show_toast") as toast,
                patch.object(emergency_ops, "now_text", return_value="2026-08-06 14:00:00"),
            ):
                result = emergency_ops.execute_selected_emergency_stop(window, [selected])

            saved = read_json_dict(selected[0] / "state.json")
            self.assertEqual(("000001 테스트",), result["changed"])
            self.assertEqual((), result["failed"])
            self.assertEqual("EMERGENCY_STOPPED", saved["status"])
            self.assertFalse(saved["trade_enabled"])
            self.assertNotIn("buy_enabled", saved)
            self.assertNotIn("sell_enabled", saved)
            self.assertEqual(before_untouched, read_json_dict(untouched[0] / "state.json"))
            window.refresh_all.assert_called_once_with()
            toast.assert_called_once()

    def test_stop_write_failure_is_reported_without_success_toast(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            target = self._target(root, "000001")
            window = self._window()
            with (
                patch.object(emergency_ops, "write_state_json", return_value=False),
                patch.object(emergency_ops.QMessageBox, "critical"),
                patch.object(emergency_ops, "append_changelog"),
                patch.object(emergency_ops, "append_stock_log"),
                patch.object(emergency_ops, "show_toast") as toast,
            ):
                result = emergency_ops.execute_selected_emergency_stop(window, [target])

        self.assertEqual(("000001 테스트",), result["failed"])
        self.assertEqual((), result["changed"])
        toast.assert_not_called()

    def test_stop_readback_mismatch_is_reported_as_failure(self) -> None:
        target = (Path("stocks/000001_TEST"), "000001", "테스트")
        initial = {"status": "STOPPED"}
        window = self._window()
        with (
            patch.object(
                emergency_ops,
                "read_json_dict",
                side_effect=[dict(initial), dict(initial), dict(initial)],
            ),
            patch.object(emergency_ops, "write_state_json", return_value=True),
            patch.object(emergency_ops, "_routine_name_for_emergency_release", return_value=""),
            patch.object(emergency_ops, "append_changelog"),
            patch.object(emergency_ops, "append_stock_log"),
            patch.object(emergency_ops, "show_toast") as toast,
        ):
            result = emergency_ops.execute_selected_emergency_stop(window, [target])

        self.assertEqual(("000001 테스트",), result["failed"])
        self.assertEqual((), result["changed"])
        toast.assert_not_called()

    def test_release_after_safety_check_returns_selected_target_to_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            target = self._target(root, "000001", status="EMERGENCY_STOPPED")
            untouched = self._target(root, "000002", status="EMERGENCY_STOPPED")
            before_untouched = read_json_dict(untouched[0] / "state.json")
            window = self._window()
            with (
                patch.object(emergency_ops, "emergency_review_reason_for_stock", return_value=(False, "정상")),
                patch.object(emergency_ops, "append_changelog"),
                patch.object(emergency_ops, "append_stock_log"),
                patch.object(emergency_ops, "show_toast") as toast,
            ):
                result = emergency_ops.execute_selected_emergency_release(window, [target])

            saved = read_json_dict(target[0] / "state.json")
            self.assertEqual(("000001 테스트",), result["normal"])
            self.assertEqual("STOPPED", saved["status"])
            self.assertFalse(saved["review_required"])
            self.assertFalse(saved["trade_enabled"])
            self.assertNotIn("buy_enabled", saved)
            self.assertNotIn("sell_enabled", saved)
            self.assertEqual(before_untouched, read_json_dict(untouched[0] / "state.json"))
            toast.assert_called_once()

    def test_release_safety_failure_moves_only_target_to_review(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            target = self._target(root, "000001", status="EMERGENCY_STOPPED")
            window = self._window()
            with (
                patch.object(emergency_ops, "emergency_review_reason_for_stock", return_value=(True, "보유잔량 존재")),
                patch.object(emergency_ops, "append_changelog"),
                patch.object(emergency_ops, "append_stock_log"),
                patch.object(emergency_ops, "show_toast"),
            ):
                result = emergency_ops.execute_selected_emergency_release(window, [target])

            saved = read_json_dict(target[0] / "state.json")
            self.assertEqual(("000001 테스트",), result["review"])
            self.assertEqual("EMERGENCY_STOPPED", saved["status"])
            self.assertTrue(saved["review_required"])
            self.assertEqual("PENDING", saved["review_status"])
            self.assertFalse(saved["trade_enabled"])

    def test_release_readback_mismatch_is_reported_as_failure(self) -> None:
        target = (Path("stocks/000001_TEST"), "000001", "테스트")
        initial = {"status": "EMERGENCY_STOPPED"}
        window = self._window()
        with (
            patch.object(
                emergency_ops,
                "read_json_dict",
                side_effect=[
                    dict(initial),
                    dict(initial),
                    dict(initial),
                    dict(initial),
                    dict(initial),
                    dict(initial),
                ],
            ),
            patch.object(emergency_ops, "write_state_json", return_value=True),
            patch.object(emergency_ops, "emergency_review_reason_for_stock", return_value=(False, "정상")),
            patch.object(emergency_ops, "_routine_name_for_emergency_release", return_value=""),
            patch.object(emergency_ops, "append_changelog"),
            patch.object(emergency_ops, "append_stock_log"),
            patch.object(emergency_ops, "show_toast") as toast,
        ):
            result = emergency_ops.execute_selected_emergency_release(window, [target])

        self.assertEqual(("000001 테스트",), result["failed"])
        self.assertEqual((), result["normal"])
        toast.assert_not_called()

    def test_common_restore_releases_review_and_preserves_routine_identity(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            stock_dir, code, name = self._target(root, "000001", status="EMERGENCY_STOPPED")
            config = {
                "routine_instance_name": "지표추종매매-A",
                "assigned_routine_instance_id": "instance-a",
            }
            (stock_dir / "config.json").write_text(
                json.dumps(config, ensure_ascii=False), encoding="utf-8"
            )
            state = read_json_dict(stock_dir / "state.json")
            state.update(
                {
                    "review_required": True,
                    "review_status": "PENDING",
                    "review_reason": "기존 사유",
                }
            )
            (stock_dir / "state.json").write_text(
                json.dumps(state, ensure_ascii=False), encoding="utf-8"
            )

            with patch.object(emergency_ops, "append_stock_log"):
                result = emergency_ops.normalize_review_emergency_target(
                    self._window(), stock_dir, code, name, destination="RESTORE"
                )

            saved = read_json_dict(stock_dir / "state.json")
            self.assertEqual("NORMALIZED", result["status"])
            self.assertEqual("STOPPED", saved["status"])
            self.assertFalse(saved["review_required"])
            self.assertEqual("지표추종매매-A", saved["review_routine"])
            self.assertEqual(config, read_json_dict(stock_dir / "config.json"))

    def test_common_unassigned_releases_review_and_clears_routine_without_deleting_stock(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            stock_dir, code, name = self._target(root, "000001", status="REVIEW_REQUIRED")
            state = read_json_dict(stock_dir / "state.json")
            state.update(
                {
                    "review_required": True,
                    "review_status": "PENDING",
                    "active_routine": "지표추종매매-A",
                    "routine_name": "지표추종매매-A",
                }
            )
            (stock_dir / "state.json").write_text(
                json.dumps(state, ensure_ascii=False), encoding="utf-8"
            )

            with (
                patch.object(emergency_ops, "append_stock_log"),
                patch.object(emergency_ops, "update_base_stock_routines", return_value=True) as unassign,
            ):
                result = emergency_ops.normalize_review_emergency_target(
                    self._window(), stock_dir, code, name, destination="UNASSIGNED"
                )

            saved = read_json_dict(stock_dir / "state.json")
            self.assertEqual("NORMALIZED", result["status"])
            self.assertEqual("STOPPED", saved["status"])
            self.assertFalse(saved["review_required"])
            self.assertEqual("", saved["active_routine"])
            self.assertEqual("", saved["routine_name"])
            self.assertTrue(stock_dir.exists())
            unassign.assert_called_once_with(code, name, [])

    def test_common_restore_blocks_without_release_or_routine_change(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            stock_dir, code, name = self._target(root, "000001", status="EMERGENCY_STOPPED")
            state = read_json_dict(stock_dir / "state.json")
            state.update(
                {
                    "review_required": True,
                    "review_status": "PENDING",
                    "active_routine": "지표추종매매-A",
                }
            )
            (stock_dir / "state.json").write_text(
                json.dumps(state, ensure_ascii=False), encoding="utf-8"
            )
            before = read_json_dict(stock_dir / "state.json")

            with (
                patch.object(
                    emergency_ops,
                    "emergency_review_reason_for_stock",
                    return_value=(True, "보유잔량 존재"),
                ),
                patch.object(emergency_ops, "update_base_stock_routines") as unassign,
            ):
                result = emergency_ops.normalize_review_emergency_target(
                    self._window(), stock_dir, code, name, destination="RESTORE"
                )

            self.assertEqual("BLOCKED", result["status"])
            self.assertEqual(before, read_json_dict(stock_dir / "state.json"))
            unassign.assert_not_called()

    def test_common_unassigned_blocks_without_release_or_routine_change(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            stock_dir, code, name = self._target(root, "000001", status="REVIEW_REQUIRED")
            state = read_json_dict(stock_dir / "state.json")
            state.update(
                {
                    "review_required": True,
                    "review_status": "PENDING",
                    "active_routine": "지표추종매매-A",
                }
            )
            (stock_dir / "state.json").write_text(
                json.dumps(state, ensure_ascii=False), encoding="utf-8"
            )
            before = read_json_dict(stock_dir / "state.json")

            with (
                patch.object(
                    emergency_ops,
                    "emergency_review_reason_for_stock",
                    return_value=(True, "보유잔량 존재"),
                ),
                patch.object(emergency_ops, "update_base_stock_routines") as unassign,
            ):
                result = emergency_ops.normalize_review_emergency_target(
                    self._window(), stock_dir, code, name, destination="UNASSIGNED"
                )

            self.assertEqual("BLOCKED", result["status"])
            self.assertEqual(before, read_json_dict(stock_dir / "state.json"))
            unassign.assert_not_called()

    def test_setting_window_callers_pass_current_selection_snapshot(self) -> None:
        target = (Path("stocks/000001_TEST"), "000001", "테스트")
        window = SimpleNamespace(selected_stock_infos=Mock(return_value=[target]))
        with (
            patch.object(emergency_ops, "execute_selected_emergency_stop", return_value={"ok": True}) as stop,
            patch.object(emergency_ops, "execute_selected_emergency_release", return_value={"ok": True}) as release,
        ):
            AutoTradeSettingWindow.emergency_stop_selected_auto_trade_stocks(window)
            AutoTradeSettingWindow.release_selected_emergency_stopped_auto_trade_stocks(window)

        stop.assert_called_once_with(window, [target])
        release.assert_called_once_with(window, [target])


if __name__ == "__main__":
    unittest.main()
