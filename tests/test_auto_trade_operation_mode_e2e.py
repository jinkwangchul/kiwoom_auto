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
from manual_ats_runtime import (
    clear_manual_ats_runtime_selection,
    current_program_session_id,
)
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
                                "program_session_id": current_program_session_id(),
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
            showAutoTradePopupMessage=Mock(),
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
                patch.object(status_ops.QMessageBox, "warning") as warning,
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
        window.statusBarMessage.assert_not_called()
        window.showAutoTradePopupMessage.assert_not_called()
        warning.assert_not_called()

    def test_all_stocks_scope_changes_single_selected_stock_without_routine_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = self._stock(Path(temp))
            window, parent = self._window(stock_dir)
            window._all_stocks_scope_active = True
            window.current_selected_routine_name = lambda: ""

            with (
                patch.object(status_ops, "append_stock_log"),
                patch.object(status_ops, "append_changelog") as append_changelog,
                patch.object(status_ops.QMessageBox, "warning") as warning,
            ):
                status_ops.auto_trade_set_selected_operation_mode(window, "CONTINUOUS")

            saved = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))

        self.assertEqual("CONTINUOUS", saved["operation_mode"])
        self.assertIn("종목별 운영방식 변경: 전체 -> 수동", append_changelog.call_args.args[2])
        window.refresh_all.assert_called_once_with()
        parent.refresh_all.assert_called_once_with()
        warning.assert_not_called()

    def test_no_stock_selection_keeps_single_selection_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = self._stock(Path(temp))
            window, _parent = self._window(stock_dir)
            window.selected_stock_infos = lambda: []
            window.update_stock_operation_mode = Mock()

            with patch.object(status_ops.QMessageBox, "warning") as warning:
                status_ops.auto_trade_set_selected_operation_mode(window, "CONTINUOUS")

        window.update_stock_operation_mode.assert_not_called()
        warning.assert_called_once_with(
            window,
            "선택 오류",
            "운영방식 변경은 종목을 1개 이상 선택해야 합니다.",
        )

    def test_active_ats_blocks_mode_change_and_preserves_runtime_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = self._stock(Path(temp), mode="CONTINUOUS", with_ats=True)
            window, _parent = self._window(stock_dir)
            with (
                patch.object(status_ops, "append_stock_log"),
                patch.object(status_ops, "append_changelog"),
                patch.object(status_ops.QMessageBox, "warning") as warning,
            ):
                status_ops.auto_trade_set_selected_operation_mode(window, "SCHEDULED")
            config = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))
            state = json.loads((stock_dir / "state.json").read_text(encoding="utf-8"))

        self.assertEqual("CONTINUOUS", config["operation_mode"])
        self.assertIn("manual_ats_selection", state)
        warning.assert_called_once_with(
            window,
            "운영방식 변경",
            "선택한 종목을 변경할 수 없습니다.",
        )
        window.statusBarMessage.assert_not_called()
        window.showAutoTradePopupMessage.assert_not_called()

    def test_scheduled_to_manual_does_not_restore_previous_ats_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = self._stock(Path(temp), mode="SCHEDULED")
            window, _parent = self._window(stock_dir)
            with (
                patch.object(status_ops, "append_stock_log"),
                patch.object(status_ops, "append_changelog"),
                patch.object(status_ops.QMessageBox, "warning") as warning,
            ):
                status_ops.auto_trade_set_selected_operation_mode(window, "CONTINUOUS")
            config = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))
            state = json.loads((stock_dir / "state.json").read_text(encoding="utf-8"))

        self.assertEqual("CONTINUOUS", config["operation_mode"])
        self.assertNotIn("manual_ats_selection", state)
        window.statusBarMessage.assert_not_called()
        window.showAutoTradePopupMessage.assert_not_called()
        warning.assert_not_called()

    def test_multi_selection_calls_existing_backend_for_each_target(self) -> None:
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
                            "program_session_id": current_program_session_id(),
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
            window.update_stock_operation_mode = Mock(
                side_effect=[True, False],
            )
            with patch.object(status_ops.QMessageBox, "warning") as warning:
                result = status_ops.auto_trade_set_selected_operation_mode(
                    window,
                    "SCHEDULED",
                )
            configs = [
                json.loads((path / "config.json").read_text(encoding="utf-8"))
                for path in (first, second)
            ]
            states = [
                json.loads((path / "state.json").read_text(encoding="utf-8"))
                for path in (first, second)
            ]

        self.assertEqual(2, window.update_stock_operation_mode.call_count)
        self.assertEqual(1, result["succeeded"])
        self.assertEqual(1, result["failed"])
        self.assertTrue(all(config["operation_mode"] == "CONTINUOUS" for config in configs))
        self.assertTrue(all("manual_ats_selection" in state for state in states))
        window.statusBarMessage.assert_not_called()
        window.showAutoTradePopupMessage.assert_not_called()
        warning.assert_called_once()

    def test_context_menu_has_no_operation_mode_change_entry(self) -> None:
        source = (
            Path(status_ops.__file__).with_name("gui_auto_trade_context_menu.py")
            .read_text(encoding="utf-8")
        )
        self.assertNotIn("set_selected_operation_mode(", source)

    def test_double_click_outside_operation_column_is_ignored(self) -> None:
        from gui_auto_trade_setting_window import AutoTradeSettingWindow

        window = SimpleNamespace(
            operation_stock_dir_from_row=Mock(),
            set_selected_operation_mode=Mock(),
            showAutoTradePopupMessage=Mock(),
        )
        item = Mock()
        item.column.return_value = 1

        with patch.object(status_ops.QMessageBox, "warning") as warning:
            AutoTradeSettingWindow.on_stock_table_item_double_clicked(window, item)

        window.operation_stock_dir_from_row.assert_not_called()
        window.set_selected_operation_mode.assert_not_called()
        window.showAutoTradePopupMessage.assert_not_called()
        warning.assert_not_called()

    def test_operation_column_double_click_defers_backend_until_event_returns(self) -> None:
        from gui_auto_trade_setting_window import AutoTradeSettingWindow
        import gui_auto_trade_setting_window as setting_window

        target = (Path("stocks/111111_TEST"), "111111", "테스트")
        callbacks = []
        window = SimpleNamespace(
            stock_table=SimpleNamespace(selectRow=Mock()),
            operation_stock_dir_from_row=Mock(return_value=target[0]),
            stock_info_from_row=Mock(return_value=target),
            _stock_operation_mode_double_click_pending=False,
        )
        item = Mock()
        item.column.return_value = 2
        item.row.return_value = 3

        with (
            patch.object(setting_window.QTimer, "singleShot", side_effect=lambda _ms, callback: callbacks.append(callback)),
            patch.object(setting_window, "handle_auto_trade_operation_mode_double_click") as backend,
        ):
            AutoTradeSettingWindow.on_stock_table_item_double_clicked(window, item)

            self.assertEqual(1, len(callbacks))
            backend.assert_not_called()
            self.assertTrue(window._stock_operation_mode_double_click_pending)
            window.stock_table.selectRow.assert_called_once_with(3)

            callbacks[0]()

        backend.assert_called_once_with(window, target)
        self.assertFalse(window._stock_operation_mode_double_click_pending)

    def test_operation_column_fast_double_click_queues_backend_once(self) -> None:
        from gui_auto_trade_setting_window import AutoTradeSettingWindow
        import gui_auto_trade_setting_window as setting_window

        target = (Path("stocks/111111_TEST"), "111111", "테스트")
        callbacks = []
        window = SimpleNamespace(
            stock_table=SimpleNamespace(selectRow=Mock()),
            operation_stock_dir_from_row=Mock(return_value=target[0]),
            stock_info_from_row=Mock(return_value=target),
            _stock_operation_mode_double_click_pending=False,
        )
        item = Mock()
        item.column.return_value = 2
        item.row.return_value = 3

        with (
            patch.object(setting_window.QTimer, "singleShot", side_effect=lambda _ms, callback: callbacks.append(callback)),
            patch.object(setting_window, "handle_auto_trade_operation_mode_double_click") as backend,
        ):
            AutoTradeSettingWindow.on_stock_table_item_double_clicked(window, item)
            AutoTradeSettingWindow.on_stock_table_item_double_clicked(window, item)

            self.assertEqual(1, len(callbacks))
            backend.assert_not_called()

            callbacks[0]()

        backend.assert_called_once_with(window, target)
        self.assertFalse(window._stock_operation_mode_double_click_pending)

    def test_write_failure_does_not_report_success_and_reloads_runtime_views(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = self._stock(Path(temp))
            window, parent = self._window(stock_dir)
            window.update_stock_operation_mode = Mock(return_value=False)

            with (
                patch.object(status_ops, "append_changelog") as append_changelog,
                patch.object(status_ops.QMessageBox, "warning") as warning,
            ):
                status_ops.auto_trade_set_selected_operation_mode(window, "CONTINUOUS")

        append_changelog.assert_not_called()
        window.refresh_all.assert_called_once_with()
        parent.refresh_all.assert_called_once_with()
        window.statusBarMessage.assert_not_called()
        window.showAutoTradePopupMessage.assert_not_called()
        warning.assert_called_once_with(
            window,
            "운영방식 변경",
            "선택한 종목을 변경할 수 없습니다.",
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
                    side_effect=[dict(original), {"status": "STOPPED"}, dict(original)],
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
        critical.assert_not_called()
        self.assertIn("read-back 실패", append_stock_log.call_args.args[2])

    def test_lifecycle_clear_then_allows_existing_time_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = self._stock(Path(temp), mode="CONTINUOUS", with_ats=True)
            window, _parent = self._window(stock_dir)
            self.assertTrue(clear_manual_ats_runtime_selection(stock_dir))
            with (
                patch.object(status_ops, "append_stock_log"),
                patch.object(status_ops, "append_changelog"),
                patch.object(status_ops.QMessageBox, "warning") as warning,
            ):
                status_ops.auto_trade_set_selected_operation_mode(window, "SCHEDULED")
            config = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))

        self.assertEqual("SCHEDULED", config["operation_mode"])
        window.statusBarMessage.assert_not_called()
        window.showAutoTradePopupMessage.assert_not_called()
        warning.assert_not_called()

    def test_holding_quantity_alone_does_not_block_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = self._stock(Path(temp), mode="CONTINUOUS")
            state_path = stock_dir / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["holding_qty"] = 10
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            window, _parent = self._window(stock_dir)
            with (
                patch.object(status_ops, "append_stock_log"),
                patch.object(status_ops, "append_changelog"),
            ):
                status_ops.auto_trade_set_selected_operation_mode(window, "SCHEDULED")
            saved = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))
            self.assertEqual("SCHEDULED", saved["operation_mode"])

    def test_after_schedule_end_allows_monitoring_and_completed_states(self) -> None:
        for runtime_status in (
            "MONITORING",
            "WAIT_BUY",
            "AUTO_CLOSED",
            "EARLY_CLOSED",
            "LIQUIDATED",
        ):
            with self.subTest(runtime_status=runtime_status), tempfile.TemporaryDirectory() as temp:
                stock_dir = self._stock(Path(temp), mode="CONTINUOUS")
                state_path = stock_dir / "state.json"
                state = json.loads(state_path.read_text(encoding="utf-8"))
                state["status"] = runtime_status
                state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
                window, _parent = self._window(stock_dir)
                with (
                    patch.object(
                        status_ops,
                        "current_datetime",
                        return_value=datetime(2026, 7, 25, 17, 0, 0),
                    ),
                    patch.object(status_ops, "append_stock_log"),
                    patch.object(status_ops, "append_changelog"),
                    patch.object(status_ops.QMessageBox, "warning") as warning,
                ):
                    status_ops.auto_trade_set_selected_operation_mode(window, "SCHEDULED")
                saved = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))
                self.assertEqual("SCHEDULED", saved["operation_mode"])
                warning.assert_not_called()

    def test_schedule_end_and_after_allow_idle_states(self) -> None:
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
                stock_dir = self._stock(Path(temp), mode=current_mode)
                window, _parent = self._window(stock_dir)
                with (
                    patch.object(status_ops, "current_datetime", return_value=now_dt),
                    patch.object(status_ops, "append_stock_log"),
                    patch.object(status_ops.QMessageBox, "warning") as warning,
                ):
                    status_ops.auto_trade_set_selected_operation_mode(
                        window,
                        requested_mode,
                    )
                saved = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))
                state = json.loads((stock_dir / "state.json").read_text(encoding="utf-8"))
                self.assertEqual(requested_mode, saved["operation_mode"])
                self.assertNotIn("manual_ats_selection", state)
                warning.assert_not_called()
                window.statusBarMessage.assert_not_called()
                window.showAutoTradePopupMessage.assert_not_called()

    def test_idle_scheduled_stock_can_return_to_manual_with_invalid_old_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = self._stock(
                Path(temp),
                mode="SCHEDULED",
                config_values={"end_buy_time": "invalid"},
            )
            window, _parent = self._window(stock_dir)
            with (
                patch.object(
                    status_ops,
                    "current_datetime",
                    return_value=datetime(2026, 7, 25, 17, 0, 0),
                ),
                patch.object(status_ops, "append_stock_log"),
                patch.object(status_ops, "append_changelog"),
                patch.object(status_ops.QMessageBox, "warning") as warning,
            ):
                status_ops.auto_trade_set_selected_operation_mode(window, "CONTINUOUS")
            saved = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))
            self.assertEqual("CONTINUOUS", saved["operation_mode"])
            warning.assert_not_called()

    def test_active_trading_order_and_close_states_block_change(self) -> None:
        scenarios = (
            ("RUNNING", {}, None),
            ("STOPPED", {}, {"status": "OPEN", "side": "BUY", "order_qty": 3}),
            ("AUTO_CLOSING", {}, None),
            ("EARLY_CLOSING", {}, None),
            ("LIQUIDATING", {}, None),
            (
                "MONITORING",
                {
                    "liquidation_policy_forced": True,
                    "liquidation_policy_reason": "EARLY_CLOSE",
                },
                None,
            ),
        )
        for runtime_status, state_values, order in scenarios:
            with self.subTest(runtime_status=runtime_status), tempfile.TemporaryDirectory() as temp:
                stock_dir = self._stock(Path(temp), mode="CONTINUOUS")
                state_path = stock_dir / "state.json"
                state = json.loads(state_path.read_text(encoding="utf-8"))
                state.update({"status": runtime_status, **state_values})
                state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
                if order is not None:
                    (stock_dir / "orders.json").write_text(
                        json.dumps({"orders": [order]}, ensure_ascii=False),
                        encoding="utf-8",
                    )
                window, _parent = self._window(stock_dir)
                with (
                    patch.object(
                        status_ops,
                        "current_datetime",
                        return_value=datetime(2026, 7, 25, 17, 0, 0),
                    ),
                    patch.object(status_ops, "append_stock_log"),
                    patch.object(status_ops.QMessageBox, "warning") as warning,
                ):
                    status_ops.auto_trade_set_selected_operation_mode(window, "SCHEDULED")
                saved = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))
                self.assertEqual("CONTINUOUS", saved["operation_mode"])
                warning.assert_called_once_with(
                    window,
                    "운영방식 변경",
                    "선택한 종목을 변경할 수 없습니다.",
                )

    def test_active_ats_blocks_before_and_during_session_regardless_of_holdings(self) -> None:
        scenarios = (
            (datetime(2026, 7, 25, 7, 30, 0), 0),
            (datetime(2026, 7, 25, 8, 20, 0), 0),
            (datetime(2026, 7, 25, 8, 20, 0), 10),
        )
        for now_dt, holding_qty in scenarios:
            with self.subTest(now_dt=now_dt, holding_qty=holding_qty), tempfile.TemporaryDirectory() as temp:
                stock_dir = self._stock(Path(temp), mode="CONTINUOUS", with_ats=True)
                state_path = stock_dir / "state.json"
                state = json.loads(state_path.read_text(encoding="utf-8"))
                state["holding_qty"] = holding_qty
                state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
                window, _parent = self._window(stock_dir)
                with (
                    patch.object(status_ops, "current_datetime", return_value=now_dt),
                    patch.object(status_ops, "append_stock_log"),
                    patch.object(status_ops.QMessageBox, "warning") as warning,
                ):
                    status_ops.auto_trade_set_selected_operation_mode(window, "SCHEDULED")
                config = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))
                state = json.loads(state_path.read_text(encoding="utf-8"))

                self.assertEqual("CONTINUOUS", config["operation_mode"])
                self.assertIn("manual_ats_selection", state)
                warning.assert_called_once_with(
                    window,
                    "운영방식 변경",
                    "선택한 종목을 변경할 수 없습니다.",
                )
                window.statusBarMessage.assert_not_called()
                window.showAutoTradePopupMessage.assert_not_called()

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
        active_ats = operation_mode_change_decision(
            {
                "operation_mode": "CONTINUOUS",
                "start_time": "invalid",
                "end_buy_time": "invalid",
            },
            "SCHEDULED",
            now_dt,
            global_schedule,
            ats_runtime_active=True,
        )

        self.assertEqual("INDIVIDUAL", individual["schedule_source"])
        self.assertEqual("12:30:00", individual["scheduled_end_time"])
        self.assertEqual("GLOBAL", fallback["schedule_source"])
        self.assertEqual("15:20:00", fallback["scheduled_end_time"])
        self.assertEqual("BLOCKED_TIME_POLICY_MISSING", missing["reason"])
        self.assertEqual("BLOCKED_TIME_POLICY_INVALID", invalid["reason"])
        self.assertEqual("BLOCKED_ATS_RUNTIME_ACTIVE", active_ats["reason"])
        self.assertEqual("", active_ats["schedule_source"])

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
                patch.object(status_ops.QMessageBox, "warning") as warning,
            ):
                status_ops.auto_trade_set_selected_operation_mode(window, "SCHEDULED")
            config = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))

        self.assertEqual("CONTINUOUS", config["operation_mode"])
        warning.assert_called_once_with(
            window,
            "운영방식 변경",
            "선택한 종목을 변경할 수 없습니다.",
        )
        window.statusBarMessage.assert_not_called()
        window.showAutoTradePopupMessage.assert_not_called()

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
                patch.object(status_ops.QMessageBox, "warning") as warning,
            ):
                status_ops.auto_trade_set_selected_operation_mode(window, "CONTINUOUS")

            saved = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))

        self.assertEqual("CONTINUOUS", saved["operation_mode"])
        window.refresh_all.assert_called_once_with()
        parent.refresh_all.assert_called_once_with()
        window.statusBarMessage.assert_not_called()
        window.showAutoTradePopupMessage.assert_not_called()
        warning.assert_not_called()

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
