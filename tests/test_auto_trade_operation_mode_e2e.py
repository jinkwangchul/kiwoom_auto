from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gui_auto_trade_status_ops as status_ops
import state_policy
from state_policy import operation_mode_change_decision


class AutoTradeOperationModeE2ETest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock_patch = patch.object(
            status_ops,
            "current_datetime",
            return_value=datetime(2026, 7, 25, 12, 0, 0),
        )
        self.clock_patch.start()
        self.addCleanup(self.clock_patch.stop)

    def _stock(
        self,
        root: Path,
        *,
        mode: str = "SCHEDULED",
        with_ats: bool = False,
        config_values: dict[str, object] | None = None,
    ) -> Path:
        stock_dir = root / "stocks" / "111111_테스트종목"
        stock_dir.mkdir(parents=True)
        config = {
            "operation_mode": mode,
            "start_time": "09:00:00",
            "end_buy_time": "13:30:00",
        }
        if config_values:
            config.update(config_values)
        (stock_dir / "config.json").write_text(
            json.dumps(config, ensure_ascii=False),
            encoding="utf-8",
        )
        (stock_dir / "state.json").write_text(
            json.dumps(
                {
                    "status": "STOPPED",
                    "trade_enabled": False,
                    **(
                        {
                            "manual_ats_selection": {
                                "selected_sessions": ["extra1"],
                                "trade_date": "2026-07-25",
                                "program_session_id": "program-session",
                            }
                        }
                        if with_ats
                        else {}
                    ),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return stock_dir

    def _window(self, stock_dir: Path):
        parent = SimpleNamespace(refresh_all=Mock())
        window = SimpleNamespace(
            selected_stock_infos=lambda: [(stock_dir, "111111", "테스트종목")],
            current_selected_routine_name=lambda: "테스트루틴",
            refresh_all=Mock(),
            statusBarMessage=Mock(),
            parent=lambda: parent,
            recalculate_stock_status_by_operation_policy=Mock(
                return_value=("unchanged", "STOPPED", "STOPPED")
            ),
        )
        window.update_stock_operation_mode = (
            lambda target_dir, code, name, mode, updates=None:
            status_ops.auto_trade_update_stock_operation_mode(
                window,
                target_dir,
                code,
                name,
                mode,
                updates,
            )
        )
        return window, parent

    def test_success_persists_read_back_restores_and_refreshes_both_views_once(self) -> None:
        from gui_auto_trade_setting_window import AutoTradeSettingWindow

        with tempfile.TemporaryDirectory() as temp:
            stock_dir = self._stock(Path(temp))
            window, parent = self._window(stock_dir)

            with (
                patch.object(status_ops, "append_stock_log"),
                patch.object(status_ops, "append_changelog"),
            ):
                status_ops.auto_trade_set_selected_operation_mode(window, "CONTINUOUS")

            saved = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))
            restored_modes = AutoTradeSettingWindow.selected_operation_mode_set(
                SimpleNamespace(),
                [(stock_dir, "111111", "테스트종목")],
            )

        self.assertEqual("CONTINUOUS", saved["operation_mode"])
        self.assertEqual({"CONTINUOUS"}, restored_modes)
        window.refresh_all.assert_called_once_with()
        parent.refresh_all.assert_called_once_with()
        window.statusBarMessage.assert_called_once()
        window.statusBarMessage.assert_called_once_with(
            "선택한 종목이 수동운영으로 변경되었습니다."
        )

    def test_manual_to_scheduled_clears_runtime_ats_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = self._stock(Path(temp), mode="CONTINUOUS", with_ats=True)
            window, _parent = self._window(stock_dir)
            with (
                patch.object(status_ops, "append_stock_log"),
                patch.object(status_ops, "append_changelog"),
            ):
                status_ops.auto_trade_set_selected_operation_mode(window, "SCHEDULED")
            config = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))
            state = json.loads((stock_dir / "state.json").read_text(encoding="utf-8"))

        self.assertEqual("SCHEDULED", config["operation_mode"])
        self.assertNotIn("manual_ats_selection", state)
        window.statusBarMessage.assert_called_once_with(
            "선택한 종목이 시간운영으로 변경되었습니다."
        )

    def test_scheduled_to_manual_does_not_restore_previous_ats_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = self._stock(Path(temp), mode="SCHEDULED", with_ats=True)
            window, _parent = self._window(stock_dir)
            with (
                patch.object(status_ops, "append_stock_log"),
                patch.object(status_ops, "append_changelog"),
            ):
                status_ops.auto_trade_set_selected_operation_mode(window, "CONTINUOUS")
            config = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))
            state = json.loads((stock_dir / "state.json").read_text(encoding="utf-8"))

        self.assertEqual("CONTINUOUS", config["operation_mode"])
        self.assertNotIn("manual_ats_selection", state)
        window.statusBarMessage.assert_called_once_with(
            "선택한 종목이 수동운영으로 변경되었습니다."
        )

    def test_multi_selection_is_blocked_before_backend_and_preserves_ats(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = self._stock(root, mode="CONTINUOUS", with_ats=True)
            second = root / "stocks" / "222222_두번째종목"
            second.mkdir(parents=True)
            (second / "config.json").write_text(
                '{"operation_mode":"CONTINUOUS"}',
                encoding="utf-8",
            )
            (second / "state.json").write_text(
                json.dumps(
                    {
                        "status": "STOPPED",
                        "manual_ats_selection": {
                            "selected_sessions": ["extra2"],
                            "trade_date": "2026-07-25",
                            "program_session_id": "program-session",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            window, _parent = self._window(first)
            window.selected_stock_infos = lambda: [
                (first, "111111", "테스트종목"),
                (second, "222222", "두번째종목"),
            ]
            window.update_stock_operation_mode = Mock()
            with patch.object(status_ops.QMessageBox, "warning") as warning:
                status_ops.auto_trade_set_selected_operation_mode(window, "SCHEDULED")
            configs = [
                json.loads((path / "config.json").read_text(encoding="utf-8"))
                for path in (first, second)
            ]
            states = [
                json.loads((path / "state.json").read_text(encoding="utf-8"))
                for path in (first, second)
            ]

        window.update_stock_operation_mode.assert_not_called()
        self.assertTrue(all(config["operation_mode"] == "CONTINUOUS" for config in configs))
        self.assertTrue(all("manual_ats_selection" in state for state in states))
        window.statusBarMessage.assert_not_called()
        warning.assert_called_once_with(
            window,
            "선택 오류",
            "운영방식 변경은 한 종목만 선택해야 합니다.",
        )

    def test_context_menu_has_no_operation_mode_change_entry(self) -> None:
        source = (
            Path(status_ops.__file__).with_name("gui_auto_trade_context_menu.py")
            .read_text(encoding="utf-8")
        )
        self.assertNotIn("set_selected_operation_mode(", source)

    def test_write_failure_does_not_report_success_and_reloads_runtime_views(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = self._stock(Path(temp))
            window, parent = self._window(stock_dir)
            window.update_stock_operation_mode = Mock(return_value=False)

            with patch.object(status_ops, "append_changelog") as append_changelog:
                status_ops.auto_trade_set_selected_operation_mode(window, "CONTINUOUS")

        append_changelog.assert_not_called()
        window.refresh_all.assert_called_once_with()
        parent.refresh_all.assert_called_once_with()
        window.statusBarMessage.assert_called_once_with(
            "선택한 종목을 변경할 수 없습니다."
        )

    def test_read_back_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = self._stock(Path(temp))
            window, _parent = self._window(stock_dir)
            original = {"operation_mode": "SCHEDULED"}

            with (
                patch.object(
                    status_ops,
                    "read_json_dict",
                    side_effect=[dict(original), dict(original)],
                ),
                patch.object(status_ops, "append_stock_log") as append_stock_log,
                patch.object(status_ops.QMessageBox, "critical") as critical,
            ):
                result = status_ops.auto_trade_update_stock_operation_mode(
                    window,
                    stock_dir,
                    "111111",
                    "테스트종목",
                    "CONTINUOUS",
                )

        self.assertFalse(result)
        critical.assert_called_once()
        self.assertIn("read-back 실패", append_stock_log.call_args.args[2])

    def test_ats_clear_failure_blocks_operation_mode_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = self._stock(Path(temp), mode="CONTINUOUS", with_ats=True)
            window, _parent = self._window(stock_dir)
            with (
                patch.object(
                    status_ops,
                    "clear_manual_ats_runtime_selection",
                    return_value=False,
                ),
                patch.object(status_ops, "append_stock_log") as append_stock_log,
                patch.object(status_ops.QMessageBox, "critical") as critical,
            ):
                status_ops.auto_trade_set_selected_operation_mode(window, "SCHEDULED")
            config = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))

        self.assertEqual("CONTINUOUS", config["operation_mode"])
        critical.assert_called_once()
        self.assertIn("ATS 선택 해제 실패", append_stock_log.call_args.args[2])
        window.statusBarMessage.assert_called_once_with(
            "선택한 종목을 변경할 수 없습니다."
        )

    def test_trade_quantities_do_not_block_change_before_schedule_end(self) -> None:
        scenarios = (
            {"holding_qty": 10},
            {"buy_pending_qty": 3},
            {"sell_pending_qty": 4},
        )
        for state_values in scenarios:
            with self.subTest(state_values=state_values), tempfile.TemporaryDirectory() as temp:
                stock_dir = self._stock(Path(temp), mode="CONTINUOUS")
                state_path = stock_dir / "state.json"
                state = json.loads(state_path.read_text(encoding="utf-8"))
                state.update(state_values)
                state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
                window, _parent = self._window(stock_dir)
                with (
                    patch.object(status_ops, "append_stock_log"),
                    patch.object(status_ops, "append_changelog"),
                ):
                    status_ops.auto_trade_set_selected_operation_mode(window, "SCHEDULED")
                saved = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))
                self.assertEqual("SCHEDULED", saved["operation_mode"])

    def test_exact_schedule_end_and_after_block_both_directions_before_ats_clear(self) -> None:
        scenarios = (
            ("CONTINUOUS", "SCHEDULED", datetime(2026, 7, 25, 13, 30, 0)),
            ("CONTINUOUS", "SCHEDULED", datetime(2026, 7, 25, 13, 30, 1)),
            ("SCHEDULED", "CONTINUOUS", datetime(2026, 7, 25, 13, 30, 0)),
            ("SCHEDULED", "CONTINUOUS", datetime(2026, 7, 25, 14, 0, 0)),
        )
        for current_mode, requested_mode, now_dt in scenarios:
            with (
                self.subTest(current_mode=current_mode, requested_mode=requested_mode, now_dt=now_dt),
                tempfile.TemporaryDirectory() as temp,
            ):
                stock_dir = self._stock(Path(temp), mode=current_mode, with_ats=True)
                window, _parent = self._window(stock_dir)
                with (
                    patch.object(status_ops, "current_datetime", return_value=now_dt),
                    patch.object(status_ops, "clear_manual_ats_runtime_selection") as clear_ats,
                    patch.object(status_ops, "append_stock_log"),
                    patch.object(status_ops.QMessageBox, "warning") as warning,
                ):
                    status_ops.auto_trade_set_selected_operation_mode(
                        window,
                        requested_mode,
                    )
                saved = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))
                state = json.loads((stock_dir / "state.json").read_text(encoding="utf-8"))
                self.assertEqual(current_mode, saved["operation_mode"])
                self.assertIn("manual_ats_selection", state)
                clear_ats.assert_not_called()
                self.assertIn("13:30", warning.call_args.args[2])
                window.statusBarMessage.assert_called_once_with(
                    "선택한 종목을 변경할 수 없습니다."
                )

    def test_schedule_policy_resolution_is_strict_and_individual_first(self) -> None:
        now_dt = datetime(2026, 7, 25, 12, 0, 0)
        global_schedule = {"start_time": "09:00:00", "end_buy_time": "15:20:00"}

        individual = operation_mode_change_decision(
            {
                "operation_mode": "CONTINUOUS",
                "start_time": "09:30:00",
                "end_buy_time": "12:30:00",
            },
            "SCHEDULED",
            now_dt,
            global_schedule,
        )
        fallback = operation_mode_change_decision(
            {"operation_mode": "CONTINUOUS"},
            "SCHEDULED",
            now_dt,
            global_schedule,
        )
        missing = operation_mode_change_decision(
            {"operation_mode": "CONTINUOUS"},
            "SCHEDULED",
            now_dt,
            {},
        )
        invalid = operation_mode_change_decision(
            {
                "operation_mode": "CONTINUOUS",
                "start_time": "09:00:00",
                "end_buy_time": "invalid",
            },
            "SCHEDULED",
            now_dt,
            global_schedule,
        )

        self.assertEqual("INDIVIDUAL", individual["schedule_source"])
        self.assertEqual("12:30:00", individual["scheduled_end_time"])
        self.assertEqual("GLOBAL", fallback["schedule_source"])
        self.assertEqual("15:20:00", fallback["scheduled_end_time"])
        self.assertEqual("BLOCKED_TIME_POLICY_MISSING", missing["reason"])
        self.assertEqual("BLOCKED_TIME_POLICY_INVALID", invalid["reason"])

        with tempfile.TemporaryDirectory() as temp:
            policy_path = Path(temp) / "operation_policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "scheduled_operation": {
                            "default_start_time": "08:50:00",
                            "default_end_buy_time": "14:10:00",
                        }
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.object(state_policy, "OPERATION_POLICY_PATH", policy_path),
                patch.object(
                    state_policy,
                    "GLOBAL_SCHEDULE_PATH",
                    Path(temp) / "global_schedule.json",
                ),
            ):
                production_fallback = operation_mode_change_decision(
                    {"operation_mode": "CONTINUOUS"},
                    "SCHEDULED",
                    now_dt,
                )

        self.assertEqual("GLOBAL", production_fallback["schedule_source"])
        self.assertEqual("14:10:00", production_fallback["scheduled_end_time"])

    def test_invalid_schedule_policy_uses_single_failure_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = self._stock(
                Path(temp),
                mode="CONTINUOUS",
                config_values={"end_buy_time": "invalid"},
            )
            window, _parent = self._window(stock_dir)
            with (
                patch.object(status_ops, "append_stock_log"),
                patch.object(status_ops.QMessageBox, "warning"),
            ):
                status_ops.auto_trade_set_selected_operation_mode(window, "SCHEDULED")
            config = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))

        self.assertEqual("CONTINUOUS", config["operation_mode"])
        window.statusBarMessage.assert_called_once_with(
            "선택한 종목을 변경할 수 없습니다."
        )

    def test_status_recalculation_failure_is_reported_after_mode_save(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = self._stock(Path(temp))
            window, parent = self._window(stock_dir)
            window.recalculate_stock_status_by_operation_policy.return_value = (
                "failed",
                "STOPPED",
                "RUNNING",
            )

            with (
                patch.object(status_ops, "append_stock_log"),
                patch.object(status_ops, "append_changelog"),
            ):
                status_ops.auto_trade_set_selected_operation_mode(window, "CONTINUOUS")

            saved = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))

        self.assertEqual("CONTINUOUS", saved["operation_mode"])
        window.refresh_all.assert_called_once_with()
        parent.refresh_all.assert_called_once_with()
        window.statusBarMessage.assert_called_once_with(
            "선택한 종목이 수동운영으로 변경되었습니다."
        )

    def test_cancelled_schedule_dialog_does_not_write_or_refresh(self) -> None:
        from PyQt5.QtWidgets import QDialog
        from gui_auto_trade_setting_window import AutoTradeSettingWindow

        stock_dir = Path("stocks/111111_테스트종목")
        window = SimpleNamespace(
            selected_stock_infos=lambda: [(stock_dir, "111111", "테스트종목")],
            set_selected_operation_mode=Mock(),
        )
        dialog = Mock()
        dialog.exec_.return_value = QDialog.Rejected

        with (
            patch("gui_auto_trade_setting_window.read_json_dict", return_value={"operation_mode": "SCHEDULED"}),
            patch("gui_auto_trade_setting_window.ScheduleOperationDialog", return_value=dialog),
        ):
            AutoTradeSettingWindow.set_selected_individual_schedule_time(window)

        window.set_selected_operation_mode.assert_not_called()


if __name__ == "__main__":
    unittest.main()
