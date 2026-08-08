# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QPoint, QRect, Qt
from PyQt5.QtWidgets import QApplication, QLabel, QLineEdit, QTableWidgetItem

import gui_main_table_loader as main_loader
import gui_operation_environment as environment
import gui_windows


class OperationBudgetDefaultsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_missing_policy_fields_use_backward_compatible_defaults(self) -> None:
        self.assertEqual(
            {
                "quantity": 1,
                "amount_multiplier": 1.5,
                "limit_recommended_multiplier": 100.0,
                "limit_minimum_multiplier": 25.0,
            },
            environment.starting_budget_defaults({}),
        )

    def test_policy_reader_merges_missing_budget_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy_path = Path(temp_dir) / "operation_policy.json"
            policy_path.write_text(
                json.dumps({"regular_market": {"start_time": "09:10:00"}}),
                encoding="utf-8",
            )
            with patch.object(environment, "OPERATION_POLICY_PATH", policy_path):
                loaded = environment.read_operation_policy()
        self.assertEqual("09:10:00", loaded["regular_market"]["start_time"])
        self.assertEqual(1.5, loaded["starting_budget_defaults"]["amount_multiplier"])

    def test_dialog_loads_and_builds_all_four_values(self) -> None:
        policy = environment.default_operation_policy()
        policy["starting_budget_defaults"] = {
            "quantity": 3,
            "amount_multiplier": 2.5,
            "limit_recommended_multiplier": 80.0,
            "limit_minimum_multiplier": 20.0,
        }
        with patch.object(environment, "read_operation_policy", return_value=policy):
            dialog = environment.OperationEnvironmentSettingsDialog()
        self.addCleanup(dialog.deleteLater)

        self.assertEqual("3", dialog.starting_quantity.text())
        self.assertEqual("2.5", dialog.starting_amount_multiplier.text())
        self.assertEqual("80", dialog.limit_recommended_multiplier.text())
        self.assertEqual("20", dialog.limit_minimum_multiplier.text())
        self.assertIn(
            "7. 시작 예산 설정",
            [label.text() for label in dialog.findChildren(QLabel)],
        )

        dialog.starting_quantity.setText("4")
        dialog.starting_amount_multiplier.setText("1.75")
        built = dialog.build_policy_from_widgets()
        self.assertEqual(4, built["starting_budget_defaults"]["quantity"])
        self.assertEqual(1.75, built["starting_budget_defaults"]["amount_multiplier"])

    def test_dialog_rejects_minimum_multiplier_above_recommended(self) -> None:
        with patch.object(
            environment,
            "read_operation_policy",
            return_value=environment.default_operation_policy(),
        ):
            dialog = environment.OperationEnvironmentSettingsDialog()
        self.addCleanup(dialog.deleteLater)
        dialog.limit_recommended_multiplier.setText("10")
        dialog.limit_minimum_multiplier.setText("11")
        with patch.object(environment.QMessageBox, "warning") as warning:
            self.assertIsNone(dialog._validated_starting_budget_defaults())
        warning.assert_called_once()

    def test_dialog_save_round_trip_persists_all_four_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy_path = Path(temp_dir) / "operation_policy.json"
            with (
                patch.object(environment, "OPERATION_POLICY_PATH", policy_path),
                patch.object(environment, "append_changelog"),
                patch.object(environment, "show_toast"),
            ):
                dialog = environment.OperationEnvironmentSettingsDialog()
                self.addCleanup(dialog.deleteLater)
                dialog.starting_quantity.setText("5")
                dialog.starting_amount_multiplier.setText("2.25")
                dialog.limit_recommended_multiplier.setText("120")
                dialog.limit_minimum_multiplier.setText("30")
                dialog.accept()
                loaded = environment.read_operation_policy()
        self.assertEqual(
            {
                "quantity": 5,
                "amount_multiplier": 2.25,
                "limit_recommended_multiplier": 120.0,
                "limit_minimum_multiplier": 30.0,
            },
            loaded["starting_budget_defaults"],
        )

    def test_amount_budget_floors_to_whole_shares(self) -> None:
        self.assertEqual(
            80_000,
            environment.effective_amount_starting_budget(80_000, 1.5),
        )
        self.assertIsNone(environment.effective_amount_starting_budget(None, 1.5))

    def test_limit_rounding_keeps_only_leading_place_and_rounds_up(self) -> None:
        self.assertEqual(2_000_000, environment.round_up_to_leading_place(1_234_500))
        self.assertEqual(600_000, environment.round_up_to_leading_place(534_000))
        self.assertEqual(90_000, environment.round_up_to_leading_place(83_500))

    def test_main_initial_budget_uses_default_only_when_stock_value_missing(self) -> None:
        policy = {
            "starting_budget_defaults": {
                "quantity": 2,
                "amount_multiplier": 1.5,
                "limit_recommended_multiplier": 100,
                "limit_minimum_multiplier": 25,
            }
        }
        self.assertEqual(
            2,
            main_loader.stock_initial_buy_display(
                {"trade_amount_type": "QUANTITY", "buy_qty": 0},
                policy=policy,
            )["value"],
        )
        self.assertEqual(
            7,
            main_loader.stock_initial_buy_display(
                {"trade_amount_type": "QUANTITY", "buy_qty": 7},
                policy=policy,
            )["value"],
        )
        self.assertEqual(
            80_000,
            main_loader.stock_initial_buy_display(
                {"trade_amount_type": "AMOUNT", "buy_amount": 0},
                current_price=80_000,
                policy=policy,
            )["value"],
        )
        self.assertEqual(
            120_000,
            main_loader.stock_initial_buy_display(
                {"trade_amount_type": "AMOUNT", "buy_amount": 120_000},
                current_price=80_000,
                policy=policy,
            )["value"],
        )

    def test_amount_without_explicit_value_or_current_price_displays_waiting(self) -> None:
        display = main_loader.stock_initial_buy_display(
            {"trade_amount_type": "AMOUNT", "buy_amount": 0},
            current_price=None,
        )
        self.assertEqual("대기", display["value_text"])
        self.assertNotEqual("0원", display["value_text"])

        explicit = main_loader.stock_initial_buy_display(
            {"trade_amount_type": "AMOUNT", "buy_amount": 350_000},
            current_price=None,
        )
        self.assertEqual("350,000원", explicit["value_text"])

    def test_limit_waits_then_uses_first_current_price_without_fluctuation(self) -> None:
        window = SimpleNamespace()
        stock = {
            "code": "005930",
            "name": "삼성전자",
            "stock_path": "",
            "config": {"buy_limit_enabled": True, "buy_limit_amount": None},
            "state": {},
        }
        with patch.object(
            main_loader,
            "starting_budget_defaults",
            return_value={
                "quantity": 1,
                "amount_multiplier": 1.5,
                "limit_recommended_multiplier": 100,
                "limit_minimum_multiplier": 25,
            },
        ):
            waiting_result = main_loader._routine_tree_stock_metric_values(window, stock)
            stock["state"] = {"current_price": 12_345}
            first_result = main_loader._routine_tree_stock_metric_values(window, stock)
            stock["state"] = {"current_price": 20_000}
            later_result = main_loader._routine_tree_stock_metric_values(window, stock)

        self.assertEqual("한도(대기)", waiting_result[2])
        self.assertEqual(5, len(waiting_result[0]))
        self.assertEqual("한도(2,000,000)", first_result[2])
        self.assertEqual(6, len(first_result[0]))
        self.assertEqual(first_result[2], later_result[2])
        self.assertEqual(
            {"buy_limit_enabled": True, "buy_limit_amount": None},
            stock["config"],
        )

    def test_unconfigured_limit_stays_unconfigured_even_when_price_exists(self) -> None:
        result = main_loader._routine_tree_stock_metric_values(
            SimpleNamespace(),
            {
                "code": "005930",
                "name": "삼성전자",
                "stock_path": "",
                "config": {
                    "buy_limit_enabled": False,
                    "buy_limit_amount": None,
                },
                "state": {"current_price": 12_345},
            },
        )
        self.assertEqual("한도(미설정)", result[2])
        self.assertEqual(5, len(result[0]))

    def test_explicit_limit_has_priority_without_current_price(self) -> None:
        result = main_loader._routine_tree_stock_metric_values(
            SimpleNamespace(),
            {
                "code": "005930",
                "name": "삼성전자",
                "stock_path": "",
                "config": {
                    "buy_limit_enabled": True,
                    "buy_limit_amount": 750_000,
                },
                "state": {},
            },
        )
        self.assertEqual("한도(750,000)", result[2])

    def test_main_limit_suggestion_uses_current_state_price_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stock_dir = Path(temp_dir) / "005930_삼성전자"
            stock_dir.mkdir()
            config_path = stock_dir / "config.json"
            config_path.write_text("{}", encoding="utf-8")
            (stock_dir / "state.json").write_text(
                json.dumps({"current_price": 12_345}),
                encoding="utf-8",
            )
            defaults = {
                "quantity": 1,
                "amount_multiplier": 1.5,
                "limit_recommended_multiplier": 100,
                "limit_minimum_multiplier": 25,
            }
            with patch.object(gui_windows, "starting_budget_defaults", return_value=defaults):
                recommended = gui_windows.MainWindow._stock_suggested_buy_limit(config_path)
                minimum = gui_windows.MainWindow._stock_suggested_buy_limit(
                    config_path,
                    minimum=True,
                )
            self.assertEqual(2_000_000, recommended)
            self.assertEqual(400_000, minimum)
            self.assertEqual({}, json.loads(config_path.read_text(encoding="utf-8")))

    def test_limit_below_current_minimum_is_not_written(self) -> None:
        editor = QLineEdit("300000")
        host = SimpleNamespace(
            _routine_stock_buy_limit_editor=editor,
            _routine_stock_buy_limit_edit_finishing=False,
            _routine_stock_buy_limit_editor_config_path="C:/temp/config.json",
            routine_table=SimpleNamespace(
                _editing_stock_buy_limit_path="stock",
                viewport=lambda: SimpleNamespace(update=MagicMock()),
            ),
            _stock_suggested_buy_limit=MagicMock(return_value=400_000),
            _parse_buy_limit_amount=gui_windows.MainWindow._parse_buy_limit_amount,
            _write_stock_buy_limit_config=MagicMock(),
            load_routine_table=MagicMock(),
        )
        with patch.object(gui_windows.QMessageBox, "warning") as warning:
            gui_windows.MainWindow.finish_routine_stock_buy_limit_edit(host, save=True)
        warning.assert_called_once()
        host._write_stock_buy_limit_config.assert_not_called()

    def test_blank_limit_save_preserves_existing_limit_state(self) -> None:
        editor = QLineEdit("")
        host = SimpleNamespace(
            _routine_stock_buy_limit_editor=editor,
            _routine_stock_buy_limit_edit_finishing=False,
            _routine_stock_buy_limit_editor_config_path="C:/temp/config.json",
            routine_table=SimpleNamespace(
                _editing_stock_buy_limit_path="stock",
                viewport=lambda: SimpleNamespace(update=MagicMock()),
            ),
            _parse_buy_limit_amount=gui_windows.MainWindow._parse_buy_limit_amount,
            _stock_suggested_buy_limit=MagicMock(return_value=None),
            _write_stock_buy_limit_config=MagicMock(),
            load_routine_table=MagicMock(),
        )
        gui_windows.MainWindow.finish_routine_stock_buy_limit_edit(host, save=True)
        host._write_stock_buy_limit_config.assert_not_called()

    def test_waiting_text_uses_center_alignment_in_fixed_value_slots(self) -> None:
        painter = MagicMock()
        cell_rect = QRect(0, 0, 176, 24)
        before = gui_windows._initial_buy_component_rects(cell_rect)["value"]
        gui_windows._draw_initial_buy_display(
            painter,
            cell_rect,
            {"mode": "AMOUNT", "value_text": "대기"},
        )
        drawn_rect, alignment, text = painter.drawText.call_args_list[-1].args
        self.assertEqual(before, drawn_rect)
        self.assertEqual(Qt.AlignCenter, alignment)
        self.assertEqual("대기", text)
        self.assertEqual(
            Qt.AlignCenter | Qt.AlignVCenter,
            gui_windows._main_stock_value_alignment("대기"),
        )

    def test_limit_single_click_is_delayed_and_uses_stock_path_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "buy_limit_enabled": True,
                        "buy_limit_amount": None,
                    }
                ),
                encoding="utf-8",
            )
            item = QTableWidgetItem()
            item.setData(gui_windows.ROUTINE_STOCK_PATH_ROLE, "stocks/005930_test")
            timer = MagicMock()
            table = SimpleNamespace(item=MagicMock(return_value=item))
            host = SimpleNamespace(
                routine_table=table,
                _routine_stock_buy_limit_pending_path="",
                _routine_stock_buy_limit_click_timer=timer,
                _stock_config_path_for_routine_row=MagicMock(
                    return_value=config_path
                ),
                _stock_current_price_for_config=MagicMock(return_value=12_345),
            )

            with patch.object(
                gui_windows.QApplication,
                "doubleClickInterval",
                return_value=420,
            ):
                gui_windows.MainWindow.schedule_routine_stock_buy_limit_single_click(
                    host,
                    3,
                )

        self.assertEqual(
            "stocks/005930_test",
            host._routine_stock_buy_limit_pending_path,
        )
        timer.start.assert_called_once_with(445)

    def test_unconfigured_limit_single_click_does_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "buy_limit_enabled": False,
                        "buy_limit_amount": None,
                    }
                ),
                encoding="utf-8",
            )
            timer = MagicMock()
            host = SimpleNamespace(
                _stock_config_path_for_routine_row=MagicMock(
                    return_value=config_path
                ),
                _routine_stock_buy_limit_pending_path="",
                _routine_stock_buy_limit_click_timer=timer,
            )

            gui_windows.MainWindow.schedule_routine_stock_buy_limit_single_click(
                host,
                0,
            )

        timer.start.assert_not_called()
        self.assertEqual("", host._routine_stock_buy_limit_pending_path)

    def test_enabled_limit_single_click_requires_current_price(self) -> None:
        for amount in (None, 750_000):
            with self.subTest(amount=amount), tempfile.TemporaryDirectory() as temp_dir:
                config_path = Path(temp_dir) / "config.json"
                config_path.write_text(
                    json.dumps(
                        {
                            "buy_limit_enabled": True,
                            "buy_limit_amount": amount,
                        }
                    ),
                    encoding="utf-8",
                )
                timer = MagicMock()
                host = SimpleNamespace(
                    _stock_config_path_for_routine_row=MagicMock(
                        return_value=config_path
                    ),
                    _stock_current_price_for_config=MagicMock(return_value=None),
                    _routine_stock_buy_limit_pending_path="",
                    _routine_stock_buy_limit_click_timer=timer,
                )

                gui_windows.MainWindow.schedule_routine_stock_buy_limit_single_click(
                    host,
                    0,
                )

                timer.start.assert_not_called()
                self.assertEqual("", host._routine_stock_buy_limit_pending_path)

    def test_limit_save_is_blocked_when_current_price_is_unavailable(self) -> None:
        editor = QLineEdit("750000")
        host = SimpleNamespace(
            _routine_stock_buy_limit_editor=editor,
            _routine_stock_buy_limit_edit_finishing=False,
            _routine_stock_buy_limit_editor_config_path="C:/temp/config.json",
            routine_table=SimpleNamespace(
                _editing_stock_buy_limit_path="stock",
                viewport=lambda: SimpleNamespace(update=MagicMock()),
            ),
            _parse_buy_limit_amount=gui_windows.MainWindow._parse_buy_limit_amount,
            _stock_suggested_buy_limit=MagicMock(return_value=None),
            _write_stock_buy_limit_config=MagicMock(),
            load_routine_table=MagicMock(),
        )

        gui_windows.MainWindow.finish_routine_stock_buy_limit_edit(host, save=True)

        host._write_stock_buy_limit_config.assert_not_called()
        host.load_routine_table.assert_not_called()

    def test_limit_double_click_cancels_pending_single_click_release(self) -> None:
        timer = MagicMock()
        host = SimpleNamespace(
            _routine_stock_buy_limit_click_timer=timer,
            _routine_stock_buy_limit_pending_path="stocks/005930_test",
            _routine_stock_buy_limit_suppressed_release_row=-1,
        )

        gui_windows.MainWindow.cancel_routine_stock_buy_limit_single_click(
            host,
            suppress_release_row=4,
        )

        timer.stop.assert_called_once()
        self.assertEqual("", host._routine_stock_buy_limit_pending_path)
        self.assertTrue(
            gui_windows.MainWindow.consume_routine_stock_buy_limit_release(host, 4)
        )
        self.assertFalse(
            gui_windows.MainWindow.consume_routine_stock_buy_limit_release(host, 4)
        )

    def test_limit_double_click_event_cancels_editor_before_second_release(self) -> None:
        class FakeIndex:
            def isValid(self):
                return True

            def column(self):
                return 0

            def row(self):
                return 2

            def data(self, role):
                if role == gui_windows.ROUTINE_ROW_KIND_ROLE:
                    return gui_windows.ROUTINE_ROW_STOCK
                return None

        class FakeEvent:
            def __init__(self, event_type):
                self._event_type = event_type
                self.accept = MagicMock()

            def type(self):
                return self._event_type

            def button(self):
                return Qt.LeftButton

            def pos(self):
                return QPoint(30, 10)

        index = FakeIndex()
        table = SimpleNamespace(
            indexAt=MagicMock(return_value=index),
            visualRect=MagicMock(return_value=QRect(0, 0, 500, 24)),
        )
        window = MagicMock()
        window.consume_routine_stock_buy_limit_release.side_effect = [False, True]
        window._main_routine_initial_buy_badge_enabled.return_value = True
        controller = gui_windows._RoutineTreeInteractionController.__new__(
            gui_windows._RoutineTreeInteractionController
        )
        controller.table = table
        controller.window = window
        controller._stock_metric_rect = MagicMock(return_value=QRect(20, 0, 100, 24))
        controller._stock_legacy_metric_rect = MagicMock(return_value=QRect())

        first_release = FakeEvent(QEvent.MouseButtonRelease)
        double_click = FakeEvent(QEvent.MouseButtonDblClick)
        second_release = FakeEvent(QEvent.MouseButtonRelease)
        with patch.object(gui_windows, "_routine_stock_token_rect", return_value=QRect()):
            self.assertTrue(controller.eventFilter(table, first_release))
            self.assertTrue(controller.eventFilter(table, double_click))
            self.assertTrue(controller.eventFilter(table, second_release))

        window.schedule_routine_stock_buy_limit_single_click.assert_called_once_with(2)
        window.cancel_routine_stock_buy_limit_single_click.assert_called_once_with(
            suppress_release_row=2
        )
        window.handle_routine_stock_buy_limit_double_click.assert_called_once_with(2)
        window.start_routine_stock_buy_limit_edit.assert_not_called()

    def test_limit_pending_click_resolves_current_row_by_stock_snapshot(self) -> None:
        other = QTableWidgetItem()
        other.setData(gui_windows.ROUTINE_STOCK_PATH_ROLE, "stocks/000660_other")
        target = QTableWidgetItem()
        target.setData(gui_windows.ROUTINE_STOCK_PATH_ROLE, "stocks/005930_test")
        table = SimpleNamespace(
            rowCount=MagicMock(return_value=2),
            item=MagicMock(side_effect=[other, target]),
        )
        host = SimpleNamespace(
            routine_table=table,
            _routine_stock_buy_limit_pending_path="stocks/005930_test",
            start_routine_stock_buy_limit_edit=MagicMock(),
        )

        gui_windows.MainWindow._execute_routine_stock_buy_limit_single_click(host)

        host.start_routine_stock_buy_limit_edit.assert_called_once_with(1)
        self.assertEqual("", host._routine_stock_buy_limit_pending_path)

    def test_limit_double_click_resets_waiting_and_numeric_states(self) -> None:
        for amount in (None, 750_000):
            with self.subTest(amount=amount), tempfile.TemporaryDirectory() as temp_dir:
                config_path = Path(temp_dir) / "config.json"
                config_path.write_text(
                    json.dumps(
                        {
                            "buy_limit_enabled": True,
                            "buy_limit_amount": amount,
                        }
                    ),
                    encoding="utf-8",
                )
                host = SimpleNamespace(
                    _stock_config_path_for_routine_row=MagicMock(
                        return_value=config_path
                    ),
                    finish_routine_instance_buy_limit_edit=MagicMock(),
                    finish_routine_stock_buy_limit_edit=MagicMock(),
                    _write_stock_buy_limit_config=MagicMock(),
                    load_routine_table=MagicMock(),
                )

                gui_windows.MainWindow.handle_routine_stock_buy_limit_double_click(
                    host,
                    0,
                )

                host.finish_routine_stock_buy_limit_edit.assert_called_once_with(
                    save=False
                )
                host._write_stock_buy_limit_config.assert_called_once_with(
                    config_path,
                    enabled=False,
                    amount=None,
                )
                host.load_routine_table.assert_called_once()

    def test_unconfigured_limit_double_click_activates_waiting_or_recommendation(self) -> None:
        for suggested_amount in (None, 2_000_000):
            with self.subTest(suggested_amount=suggested_amount), tempfile.TemporaryDirectory() as temp_dir:
                config_path = Path(temp_dir) / "config.json"
                config_path.write_text(
                    json.dumps(
                        {
                            "buy_limit_enabled": False,
                            "buy_limit_amount": None,
                        }
                    ),
                    encoding="utf-8",
                )
                host = SimpleNamespace(
                    _stock_config_path_for_routine_row=MagicMock(
                        return_value=config_path
                    ),
                    finish_routine_instance_buy_limit_edit=MagicMock(),
                    finish_routine_stock_buy_limit_edit=MagicMock(),
                    _stock_suggested_buy_limit=MagicMock(
                        return_value=suggested_amount
                    ),
                    _write_stock_buy_limit_config=MagicMock(),
                    load_routine_table=MagicMock(),
                )

                gui_windows.MainWindow.handle_routine_stock_buy_limit_double_click(
                    host,
                    0,
                )

                host._write_stock_buy_limit_config.assert_called_once_with(
                    config_path,
                    enabled=True,
                    amount=suggested_amount,
                )
                host.load_routine_table.assert_called_once()


if __name__ == "__main__":
    unittest.main()
