# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import gui_main_emergency_ops as emergency_ops
import gui_review_required_window as review_window
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

    @staticmethod
    def _global_window(targets) -> SimpleNamespace:
        status_bar = SimpleNamespace(showMessage=Mock())
        return SimpleNamespace(
            all_runtime_stock_dirs=lambda: [target[0] for target in targets],
            refresh_auto_trade_assignment_views=Mock(),
            refresh_all=Mock(),
            statusBar=lambda: status_bar,
            btn_emergency_stop=SimpleNamespace(setText=Mock()),
        )

    @staticmethod
    def _collect_rows_for(targets):
        records = [
            SimpleNamespace(code=code, name=name, routine="테스트루틴")
            for _stock_dir, code, name in targets
        ]
        directories = {
            (code, name): stock_dir for stock_dir, code, name in targets
        }
        repository = SimpleNamespace(
            list_stocks=lambda: records,
            resolve_stock_dir=lambda code, name: directories[(code, name)],
        )
        with (
            patch.object(review_window, "stock_repository_factory", return_value=repository),
            patch.object(
                review_window,
                "read_review_policy",
                return_value={"long_term_holding_enabled": False},
            ),
        ):
            return review_window.collect_global_review_required_rows()

    def test_global_stop_blocks_trading_without_creating_review_entry(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            target = self._target(root, "000001", status="RUNNING")
            window = self._global_window([target])
            with (
                patch.object(
                    emergency_ops,
                    "write_global_emergency_stop_state",
                    return_value={"ok": True},
                ),
                patch.object(
                    emergency_ops,
                    "read_operation_state",
                    return_value={"emergency_stop": False},
                ),
                patch.object(emergency_ops, "append_production_event"),
                patch.object(emergency_ops, "append_changelog"),
                patch.object(emergency_ops, "append_stock_log"),
                patch.object(emergency_ops, "show_toast"),
                patch.object(emergency_ops.QMessageBox, "critical"),
                patch.object(emergency_ops, "now_text", return_value="2026-08-16 10:00:00"),
            ):
                emergency_ops.execute_emergency_stop(window)

            saved = read_json_dict(target[0] / "state.json")
            self.assertEqual("EMERGENCY_STOPPED", saved["status"])
            self.assertFalse(saved["trade_enabled"])
            self.assertNotIn("review_required", saved)
            self.assertNotIn("review_status", saved)
            self.assertNotIn("review_entered_at", saved)
            self.assertEqual([], self._collect_rows_for([target]))

    def test_global_stop_preserves_preexisting_review_identity_without_reentry(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            target = self._target(root, "000001", status="REVIEW_REQUIRED")
            state = read_json_dict(target[0] / "state.json")
            state.update(
                {
                    "review_required": True,
                    "review_status": "PENDING",
                    "review_reason": "기존 사유",
                    "review_entered_at": "2026-08-15 09:00:00",
                }
            )
            (target[0] / "state.json").write_text(
                json.dumps(state, ensure_ascii=False), encoding="utf-8"
            )
            window = self._global_window([target])
            with (
                patch.object(
                    emergency_ops,
                    "write_global_emergency_stop_state",
                    return_value={"ok": True},
                ),
                patch.object(
                    emergency_ops,
                    "read_operation_state",
                    return_value={"emergency_stop": False},
                ),
                patch.object(emergency_ops, "append_production_event"),
                patch.object(emergency_ops, "append_changelog"),
                patch.object(emergency_ops, "append_stock_log"),
                patch.object(emergency_ops, "show_toast"),
                patch.object(emergency_ops.QMessageBox, "critical"),
                patch.object(emergency_ops, "now_text", return_value="2026-08-16 10:00:00"),
            ):
                emergency_ops.execute_emergency_stop(window)

            saved = read_json_dict(target[0] / "state.json")
            self.assertTrue(saved["review_required"])
            self.assertEqual("기존 사유", saved["review_reason"])
            self.assertEqual("2026-08-15 09:00:00", saved["review_entered_at"])

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
            self.assertTrue(saved["review_required"])
            self.assertEqual("PENDING", saved["review_status"])
            self.assertEqual("종목 긴급정지", saved["review_location"])
            self.assertEqual("2026-08-06 14:00:00", saved["review_entered_at"])
            self.assertNotIn("buy_enabled", saved)
            self.assertNotIn("sell_enabled", saved)
            self.assertEqual(before_untouched, read_json_dict(untouched[0] / "state.json"))
            window.refresh_all.assert_called_once_with()
            toast.assert_called_once()

    def test_selected_stop_merges_existing_review_reason_without_reentry(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            target = self._target(root, "000001", status="REVIEW_REQUIRED")
            state = read_json_dict(target[0] / "state.json")
            state.update(
                {
                    "review_required": True,
                    "review_status": "RESOLVED",
                    "review_reason": "운영 데이터 불일치",
                    "review_location": "운영 시작",
                    "review_entered_at": "2026-08-01 09:00:00",
                }
            )
            (target[0] / "state.json").write_text(
                json.dumps(state, ensure_ascii=False), encoding="utf-8"
            )
            with (
                patch.object(emergency_ops, "append_changelog"),
                patch.object(emergency_ops, "append_stock_log"),
                patch.object(emergency_ops, "show_toast"),
                patch.object(emergency_ops, "now_text", return_value="2026-08-16 14:00:00"),
            ):
                result = emergency_ops.execute_selected_emergency_stop(
                    self._window(), [target]
                )
            saved = read_json_dict(target[0] / "state.json")

        self.assertEqual(("000001 테스트",), result["changed"])
        self.assertEqual(
            "운영 데이터 불일치 / 사용자 긴급정지",
            saved["review_reason"],
        )
        self.assertEqual("2026-08-01 09:00:00", saved["review_entered_at"])
        self.assertEqual("운영 시작", saved["review_location"])
        self.assertEqual("RESOLVED", saved["review_status"])
        self.assertEqual("EMERGENCY_STOPPED", saved["status"])

    def test_stop_write_failure_is_reported_without_success_toast(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            target = self._target(root, "000001")
            window = self._window()
            with (
                patch("runtime_stock_state_mutation.write_state_json", return_value=False),
                patch.object(emergency_ops.QMessageBox, "critical"),
                patch.object(emergency_ops, "append_changelog"),
                patch.object(emergency_ops, "append_stock_log"),
                patch.object(emergency_ops, "show_toast") as toast,
            ):
                result = emergency_ops.execute_selected_emergency_stop(window, [target])

        self.assertEqual(("000001 테스트",), result["failed"])
        self.assertEqual((), result["changed"])
        toast.assert_not_called()

    def test_multi_selected_stop_reviews_only_selected_without_global_latch(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            first = self._target(root, "000001", status="RUNNING")
            second = self._target(root, "000002", status="STOPPED")
            untouched = self._target(root, "000003", status="RUNNING")
            untouched_before = read_json_dict(untouched[0] / "state.json")
            window = self._window()
            with (
                patch.object(emergency_ops, "write_global_emergency_stop_state") as global_writer,
                patch.object(emergency_ops, "append_changelog"),
                patch.object(emergency_ops, "append_stock_log"),
                patch.object(emergency_ops, "show_toast"),
                patch.object(emergency_ops, "now_text", return_value="2026-08-16 10:30:00"),
            ):
                result = emergency_ops.execute_selected_emergency_stop(
                    window, [first, second]
                )

            self.assertEqual(2, result["changed_count"])
            global_writer.assert_not_called()
            for target in (first, second):
                saved = read_json_dict(target[0] / "state.json")
                self.assertTrue(saved["review_required"])
                self.assertEqual("2026-08-16 10:30:00", saved["review_entered_at"])
            self.assertEqual(
                untouched_before, read_json_dict(untouched[0] / "state.json")
            )
            self.assertEqual(2, len(self._collect_rows_for([first, second, untouched])))

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
            patch("runtime_stock_state_mutation.write_state_json", return_value=True),
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
                patch.object(emergency_ops, "_active_queue_reason", return_value=""),
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
            self.assertEqual("긴급정지 해제", saved["review_location"])
            self.assertTrue(saved["review_entered_at"])
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
            patch("runtime_stock_state_mutation.write_state_json", return_value=True),
            patch.object(emergency_ops, "emergency_review_reason_for_stock", return_value=(False, "정상")),
            patch.object(emergency_ops, "_active_queue_reason", return_value=""),
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
            stock_dir, code, name = self._target(root, "000001", status="REVIEW_REQUIRED")
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

            with (
                patch.object(emergency_ops, "emergency_release_common_guard", return_value=(True, "")),
                patch.object(emergency_ops, "append_stock_log"),
            ):
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
                patch.object(emergency_ops, "emergency_release_common_guard", return_value=(True, "")),
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
