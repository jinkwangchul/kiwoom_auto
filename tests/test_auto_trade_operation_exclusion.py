import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QWidget

import gui_auto_trade_table_loader as table_loader
import gui_auto_trade_context_menu
from assignment_episode_linkage import assign_stock_routine
from gui_operation_ui_context import refresh_auto_trade_views
from gui_auto_trade_setting_window import (
    OPERATION_EXCLUDED_CONFIG_KEY,
    AutoTradeSettingWindow,
)
from gui_auto_trade_display import SortableTableWidgetItem
from runtime_io import read_json_dict
from stock_repository import StockRepository
from tests.participant_owner_fixture import participant_owner


class AutoTradeOperationExclusionTests(unittest.TestCase):
    ASSIGNMENT_INSTANCE_ID = "0f86470a-6368-4b31-802e-d25d6ce72a5f"
    ASSIGNMENT_GROUP_ID = "4b366f80-1bbf-4a5e-b010-f411c3620e2e"

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._participant_owner = participant_owner()
        self._participant_owner_patcher = patch(
            "gui_auto_trade_policy._auto_trade_operation_participant_owner",
            return_value=self._participant_owner,
        )
        self._participant_owner_patcher.start()
        self.addCleanup(self._participant_owner_patcher.stop)

    def _stock_dir(self, root: str) -> Path:
        stock_dir = Path(root) / "stocks" / "111111_Test"
        stock_dir.mkdir(parents=True)
        return stock_dir

    def _window(self) -> SimpleNamespace:
        return SimpleNamespace(
            statusBarMessage=Mock(),
            refresh_all=Mock(),
        )

    def _write_stopped_state(self, stock_dir: Path) -> None:
        (stock_dir / "state.json").write_text(
            json.dumps(
                {
                    "status": "STOPPED",
                    "trade_enabled": False,
                    "trade_started": False,
                    "holding_qty": 0,
                    "holding_amount": 0,
                    "avg_price": 0,
                    "pending_order": False,
                    "pending_qty": 0,
                }
            ),
            encoding="utf-8",
        )

    def _canonical_target(
        self,
        root: str,
        *,
        excluded: bool = False,
    ) -> tuple[Path, str, str]:
        stock_dir = self._stock_dir(root)
        (stock_dir / "config.json").write_text(
            json.dumps(
                {
                    OPERATION_EXCLUDED_CONFIG_KEY: excluded,
                    "operation_mode": "CONTINUOUS",
                }
            ),
            encoding="utf-8",
        )
        self._write_stopped_state(stock_dir)
        (stock_dir / "orders.json").write_text("[]", encoding="utf-8")
        return stock_dir, "111111", "Test"

    def test_refresh_after_exclusion_uses_persistent_main_owner(self) -> None:
        main = QWidget()
        main.refresh_auto_trade_assignment_views = Mock()
        window = SimpleNamespace(
            _persistent_feature_owner_ref=lambda: main,
            parent=lambda: None,
            refresh_all=Mock(),
        )

        refresh_auto_trade_views(window)

        main.refresh_auto_trade_assignment_views.assert_called_once_with()
        window.refresh_all.assert_not_called()

    def test_name_double_click_toggles_operation_exclusion_not_start(self) -> None:
        with TemporaryDirectory() as temp:
            target = self._canonical_target(temp)
            window = SimpleNamespace(
                stock_info_from_row=Mock(return_value=target),
                running_registered_operation_targets=Mock(return_value=[]),
            )
            item = SimpleNamespace(column=lambda: 1, row=lambda: 3)

            with (
                patch(
                    "gui_auto_trade_setting_window.auto_trade_toggle_stock_operation_exclusion",
                    return_value=True,
                ) as toggle_exclusion,
                patch(
                    "gui_auto_trade_setting_window.auto_trade_start_status_indicator"
                ) as start_indicator,
            ):
                AutoTradeSettingWindow.on_stock_table_name_item_double_clicked(
                    window,
                    item,
                )

        toggle_exclusion.assert_called_once_with(
            window,
            target,
            refresh=False,
        )
        start_indicator.assert_not_called()

    def test_name_double_click_while_running_blocks_command_and_start(self) -> None:
        target = (Path("stocks/111111_Test"), "111111", "Test")
        window = SimpleNamespace(
            stock_info_from_row=Mock(return_value=target),
            running_registered_operation_targets=Mock(return_value=[target]),
            statusBarMessage=Mock(),
        )
        item = SimpleNamespace(column=lambda: 1, row=lambda: 3)

        with (
            patch(
                "gui_auto_trade_setting_window.auto_trade_toggle_stock_operation_exclusion",
                return_value=False,
            ) as toggle_exclusion,
            patch("gui_auto_trade_setting_window.auto_trade_start_status_indicator") as start_indicator,
            patch("gui_auto_trade_run_control.auto_trade_start_selected_auto_trades") as start_backend,
        ):
            AutoTradeSettingWindow.on_stock_table_name_item_double_clicked(
                window,
                item,
            )

        toggle_exclusion.assert_not_called()
        start_indicator.assert_not_called()
        start_backend.assert_not_called()
        window.statusBarMessage.assert_called_once()

    def test_non_name_double_click_does_not_toggle_operation_exclusion(self) -> None:
        for column in (0, 2, 3, 4):
            with self.subTest(column=column):
                window = SimpleNamespace(
                    stock_info_from_row=Mock(),
                    running_registered_operation_targets=Mock(return_value=[]),
                )
                item = SimpleNamespace(column=lambda: column, row=lambda: 3)

                with patch(
                    "gui_auto_trade_setting_window.auto_trade_toggle_stock_operation_exclusion"
                ) as toggle_exclusion:
                    AutoTradeSettingWindow.on_stock_table_name_item_double_clicked(
                        window,
                        item,
                    )

                window.stock_info_from_row.assert_not_called()
                toggle_exclusion.assert_not_called()

    def test_toggle_operation_exclusion_persists_without_touching_operation_mode(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = self._stock_dir(temp)
            config_path = stock_dir / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "operation_mode": "CONTINUOUS",
                        "assigned_routine_instance_id": "inst-a",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            self._write_stopped_state(stock_dir)
            window = self._window()
            target = (stock_dir, "111111", "Test")

            with (
                patch("gui_auto_trade_status_ops.append_stock_log"),
                patch("gui_auto_trade_status_ops.append_changelog"),
                patch("gui_auto_trade_status_ops.now_text", return_value="now"),
                patch("gui_auto_trade_status_ops.show_toast") as toast,
            ):
                enabled = AutoTradeSettingWindow.toggle_stock_operation_exclusion(
                    window,
                    target,
                )
                disabled = AutoTradeSettingWindow.toggle_stock_operation_exclusion(
                    window,
                    target,
                )

            saved = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertTrue(enabled)
            self.assertTrue(disabled)
            self.assertFalse(saved[OPERATION_EXCLUDED_CONFIG_KEY])
            self.assertEqual("CONTINUOUS", saved["operation_mode"])
            self.assertEqual("inst-a", saved["assigned_routine_instance_id"])
            self.assertEqual(2, window.refresh_all.call_count)
            self.assertEqual(2, toast.call_count)
            self.assertEqual("운영종목에서 제외됐습니다.", toast.call_args_list[0].args[1])
            self.assertEqual("운영종목으로 전환됐습니다.", toast.call_args_list[1].args[1])

    def test_current_running_stock_cannot_bypass_operation_exclusion_mutation_guard(
        self,
    ) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = self._stock_dir(temp)
            config_path = stock_dir / "config.json"
            state_path = stock_dir / "state.json"
            config_path.write_text(
                json.dumps(
                    {
                        OPERATION_EXCLUDED_CONFIG_KEY: False,
                        "operation_mode": "CONTINUOUS",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            state_path.write_text(
                json.dumps({"status": "RUNNING", "trade_enabled": True}),
                encoding="utf-8",
            )
            before = config_path.read_bytes()
            target = (stock_dir, "111111", "Test")
            window = self._window()
            window.running_registered_operation_targets = Mock(return_value=[target])

            with (
                patch("gui_auto_trade_status_ops.append_stock_log") as stock_log,
                patch("gui_auto_trade_status_ops.append_changelog") as changelog,
                patch("gui_auto_trade_status_ops.show_toast") as toast,
            ):
                changed = AutoTradeSettingWindow.set_stock_operation_exclusion(
                    window,
                    target,
                    True,
                )

            self.assertFalse(changed)
            self.assertEqual(before, config_path.read_bytes())
            window.statusBarMessage.assert_called_once()
            window.refresh_all.assert_not_called()
            stock_log.assert_not_called()
            changelog.assert_not_called()
            toast.assert_not_called()

    def test_stopped_stock_can_be_excluded_while_another_stock_is_running(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = self._stock_dir(temp)
            config_path = stock_dir / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        OPERATION_EXCLUDED_CONFIG_KEY: False,
                        "operation_mode": "CONTINUOUS",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            (stock_dir / "state.json").write_text(
                json.dumps({"status": "STOPPED", "trade_enabled": False}),
                encoding="utf-8",
            )
            target = (stock_dir, "111111", "Test")
            window = self._window()
            window.running_registered_operation_targets = Mock(
                return_value=[(Path(temp) / "222222_Run", "222222", "Run")]
            )

            with (
                patch("gui_auto_trade_status_ops.append_stock_log"),
                patch("gui_auto_trade_status_ops.append_changelog"),
                patch("gui_auto_trade_status_ops.show_toast"),
            ):
                changed = AutoTradeSettingWindow.set_stock_operation_exclusion(
                    window,
                    target,
                    True,
                )

            self.assertTrue(changed)
            self.assertTrue(
                json.loads(config_path.read_text(encoding="utf-8"))[
                    OPERATION_EXCLUDED_CONFIG_KEY
                ]
            )
            window.refresh_all.assert_called_once_with()

    def test_clear_selected_operation_exclusions_uses_existing_config_path(self) -> None:
        with TemporaryDirectory() as temp:
            first_dir = self._stock_dir(temp)
            second_dir = first_dir.parent / "222222_Other"
            second_dir.mkdir()
            for stock_dir in (first_dir, second_dir):
                (stock_dir / "config.json").write_text(
                    json.dumps(
                        {
                            OPERATION_EXCLUDED_CONFIG_KEY: True,
                            "operation_mode": "CONTINUOUS",
                            "assigned_routine_instance_id": "inst-a",
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                (stock_dir / "state.json").write_text(
                    json.dumps({"status": "STOPPED"}),
                    encoding="utf-8",
                )

            window = self._window()
            window.selected_stock_infos = Mock(
                return_value=[
                    (first_dir, "111111", "Test"),
                    (second_dir, "222222", "Other"),
                ]
            )
            window.set_stock_operation_exclusion = MethodType(
                AutoTradeSettingWindow.set_stock_operation_exclusion,
                window,
            )

            with (
                patch("gui_auto_trade_status_ops.append_stock_log"),
                patch("gui_auto_trade_status_ops.append_changelog"),
                patch("gui_auto_trade_status_ops.now_text", return_value="now"),
                patch("gui_auto_trade_status_ops.show_toast") as toast,
            ):
                AutoTradeSettingWindow.clear_selected_stock_operation_exclusions(window)

            for stock_dir in (first_dir, second_dir):
                saved = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))
                self.assertFalse(saved[OPERATION_EXCLUDED_CONFIG_KEY])
                self.assertEqual("CONTINUOUS", saved["operation_mode"])
                self.assertEqual("inst-a", saved["assigned_routine_instance_id"])
            window.refresh_all.assert_called_once_with()
            toast.assert_called_once_with(window, "2개 종목의 운영제외를 해제했습니다.")

    def test_clear_selected_operation_exclusions_does_not_bypass_review_required(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = self._stock_dir(temp)
            (stock_dir / "config.json").write_text(
                json.dumps({OPERATION_EXCLUDED_CONFIG_KEY: True}, ensure_ascii=False),
                encoding="utf-8",
            )
            (stock_dir / "state.json").write_text(
                json.dumps({"status": "REVIEW_REQUIRED"}, ensure_ascii=False),
                encoding="utf-8",
            )
            window = self._window()
            window.selected_stock_infos = Mock(
                return_value=[(stock_dir, "111111", "Test")]
            )
            window.set_stock_operation_exclusion = MethodType(
                AutoTradeSettingWindow.set_stock_operation_exclusion,
                window,
            )

            with patch("gui_auto_trade_status_ops.show_toast") as toast:
                AutoTradeSettingWindow.clear_selected_stock_operation_exclusions(window)

            saved = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))
            self.assertTrue(saved[OPERATION_EXCLUDED_CONFIG_KEY])
            window.refresh_all.assert_not_called()
            toast.assert_called_once_with(window, "운영제외 해제에 실패했습니다.")

    def test_set_selected_operation_exclusions_uses_existing_config_path(self) -> None:
        with TemporaryDirectory() as temp:
            first_dir = self._stock_dir(temp)
            second_dir = first_dir.parent / "222222_Other"
            second_dir.mkdir()
            third_dir = first_dir.parent / "333333_Third"
            third_dir.mkdir()
            for stock_dir in (first_dir, second_dir, third_dir):
                (stock_dir / "config.json").write_text(
                    json.dumps(
                        {
                            OPERATION_EXCLUDED_CONFIG_KEY: False,
                            "operation_mode": "CONTINUOUS",
                            "assigned_routine_instance_id": "inst-a",
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                (stock_dir / "state.json").write_text(
                    json.dumps({"status": "STOPPED"}),
                    encoding="utf-8",
                )

            window = self._window()
            main = QWidget()
            main.refresh_auto_trade_assignment_views = Mock()
            window._persistent_feature_owner_ref = lambda: main
            window.parent = lambda: None
            window.selected_stock_infos = Mock(
                return_value=[
                    (first_dir, "111111", "Test"),
                    (second_dir, "222222", "Other"),
                    (third_dir, "333333", "Third"),
                ]
            )
            window.set_stock_operation_exclusion = MethodType(
                AutoTradeSettingWindow.set_stock_operation_exclusion,
                window,
            )

            with (
                patch("gui_auto_trade_status_ops.append_stock_log"),
                patch("gui_auto_trade_status_ops.append_changelog"),
                patch("gui_auto_trade_status_ops.now_text", return_value="now"),
                patch("gui_auto_trade_status_ops.show_toast") as toast,
            ):
                AutoTradeSettingWindow.set_selected_stock_operation_exclusions(window)

            for stock_dir in (first_dir, second_dir, third_dir):
                saved = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))
                self.assertTrue(saved[OPERATION_EXCLUDED_CONFIG_KEY])
                self.assertEqual("CONTINUOUS", saved["operation_mode"])
                self.assertEqual("inst-a", saved["assigned_routine_instance_id"])
            main.refresh_auto_trade_assignment_views.assert_called_once_with()
            window.refresh_all.assert_not_called()
            toast.assert_called_once_with(window, "3개 종목을 운영제외했습니다.")
            main.close()

    def test_set_selected_operation_exclusions_does_not_bypass_review_required(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = self._stock_dir(temp)
            (stock_dir / "config.json").write_text(
                json.dumps({OPERATION_EXCLUDED_CONFIG_KEY: False}, ensure_ascii=False),
                encoding="utf-8",
            )
            (stock_dir / "state.json").write_text(
                json.dumps({"status": "REVIEW_REQUIRED"}, ensure_ascii=False),
                encoding="utf-8",
            )
            window = self._window()
            window.selected_stock_infos = Mock(
                return_value=[(stock_dir, "111111", "Test")]
            )
            window.set_stock_operation_exclusion = MethodType(
                AutoTradeSettingWindow.set_stock_operation_exclusion,
                window,
            )

            with patch("gui_auto_trade_status_ops.show_toast") as toast:
                AutoTradeSettingWindow.set_selected_stock_operation_exclusions(window)

            saved = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))
            self.assertFalse(saved[OPERATION_EXCLUDED_CONFIG_KEY])
            window.refresh_all.assert_not_called()
            toast.assert_called_once_with(window, "운영제외에 실패했습니다.")

    def test_running_double_click_keeps_included_config_and_state_unchanged(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = self._stock_dir(temp)
            config_path = stock_dir / "config.json"
            state_path = stock_dir / "state.json"
            config_path.write_text(
                json.dumps(
                    {
                        OPERATION_EXCLUDED_CONFIG_KEY: False,
                        "operation_mode": "CONTINUOUS",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            state_path.write_text(
                json.dumps({"status": "RUNNING", "trade_enabled": True}),
                encoding="utf-8",
            )
            before_config = config_path.read_text(encoding="utf-8")
            before_state = state_path.read_text(encoding="utf-8")
            target = (stock_dir, "111111", "Test")
            window = SimpleNamespace(
                stock_info_from_row=Mock(return_value=target),
                running_registered_operation_targets=Mock(return_value=[target]),
                statusBarMessage=Mock(),
            )
            item = SimpleNamespace(column=lambda: 1, row=lambda: 0)

            AutoTradeSettingWindow.on_stock_table_name_item_double_clicked(
                window,
                item,
            )

            self.assertEqual(before_config, config_path.read_text(encoding="utf-8"))
            self.assertEqual(before_state, state_path.read_text(encoding="utf-8"))
            window.statusBarMessage.assert_called_once()

    def test_non_running_double_click_is_not_blocked_by_another_running_stock(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = self._stock_dir(temp)
            config_path = stock_dir / "config.json"
            state_path = stock_dir / "state.json"
            config_path.write_text(
                json.dumps(
                    {
                        OPERATION_EXCLUDED_CONFIG_KEY: True,
                        "operation_mode": "CONTINUOUS",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            state_path.write_text(
                json.dumps({"status": "STOPPED", "trade_enabled": False}),
                encoding="utf-8",
            )
            before_state = state_path.read_text(encoding="utf-8")
            target = (stock_dir, "111111", "Test")
            window = SimpleNamespace(
                stock_info_from_row=Mock(return_value=target),
                running_registered_operation_targets=Mock(
                    return_value=[(Path("stocks/000001_Run"), "000001", "Run")]
                ),
                statusBarMessage=Mock(),
            )
            item = SimpleNamespace(column=lambda: 1, row=lambda: 0)

            with (
                patch("gui_auto_trade_status_ops.append_changelog"),
                patch("gui_auto_trade_status_ops.show_toast"),
            ):
                AutoTradeSettingWindow.on_stock_table_name_item_double_clicked(
                    window,
                    item,
                )

            config_after = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertFalse(config_after[OPERATION_EXCLUDED_CONFIG_KEY])
            self.assertEqual(before_state, state_path.read_text(encoding="utf-8"))
            window.statusBarMessage.assert_called_once()

    def test_toggle_operation_exclusion_does_not_toast_on_write_failure(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = self._stock_dir(temp)
            config_path = stock_dir / "config.json"
            config_path.write_text(
                json.dumps({"operation_mode": "CONTINUOUS"}) + "\n",
                encoding="utf-8",
            )
            self._write_stopped_state(stock_dir)
            window = self._window()
            target = (stock_dir, "111111", "Test")

            with (
                patch.object(
                    StockRepository,
                    "_atomic_write_stock_config",
                    side_effect=OSError("boom"),
                ),
                patch("gui_auto_trade_status_ops.QMessageBox.critical") as critical,
                patch("gui_auto_trade_status_ops.show_toast") as toast,
            ):
                result = AutoTradeSettingWindow.toggle_stock_operation_exclusion(
                    window,
                    target,
                )

            self.assertFalse(result)
            critical.assert_called_once()
            toast.assert_not_called()
            window.refresh_all.assert_not_called()

    def test_operation_excluded_row_style_keeps_item_selectable_and_enabled(self) -> None:
        item = SortableTableWidgetItem("Test")
        original_flags = item.flags()

        table_loader.apply_auto_trade_operation_excluded_row_style(item, True)

        self.assertEqual("#afb2b9", item.foreground().color().name())
        self.assertEqual("#f4f5f7", item.background().color().name())
        self.assertEqual(original_flags, item.flags())
        self.assertTrue(item.flags() & Qt.ItemIsSelectable)
        self.assertTrue(item.flags() & Qt.ItemIsEnabled)
        self.assertIn("운영 제외", item.toolTip())

    def test_review_required_row_style_overrides_operation_excluded_style(self) -> None:
        from gui_auto_trade_display import apply_auto_trade_setting_protection_row_style

        item = SortableTableWidgetItem("Review")
        original_flags = item.flags()

        apply_auto_trade_setting_protection_row_style(
            item,
            review_required=True,
            operation_excluded=True,
        )

        self.assertEqual("#ff8c00", item.foreground().color().name())
        self.assertEqual("#ffffff", item.background().color().name())
        self.assertEqual(original_flags, item.flags())
        self.assertIn("검토관리", item.toolTip())
        self.assertNotEqual("#afb2b9", item.foreground().color().name())

    def test_stock_context_menu_dispatches_emergency_stop_action(self) -> None:
        class _Action:
            def __init__(self, text: str) -> None:
                self.text = text
                self.enabled = True

            def setEnabled(self, enabled: bool) -> None:
                self.enabled = bool(enabled)

            def setText(self, text: str) -> None:
                self.text = text

            def setIcon(self, _icon) -> None:
                pass

            def setProperty(self, _name: str, _value: object) -> None:
                pass

        class _Menu:
            chosen_action = None

            def __init__(self, _parent=None) -> None:
                self.actions: list[_Action] = []

            def setToolTipsVisible(self, _visible: bool) -> None:
                pass

            def addAction(self, text: str) -> _Action:
                action = _Action(text)
                self.actions.append(action)
                if text == "검토정지":
                    _Menu.chosen_action = action
                return action

            def addMenu(self, _text: str):
                return _Menu()

            def addSeparator(self) -> None:
                pass

            def setEnabled(self, _enabled: bool) -> None:
                pass

            def exec_(self, _pos):
                return _Menu.chosen_action

        with TemporaryDirectory() as temp:
            target = self._canonical_target(temp)
            window = SimpleNamespace(
                stock_table=SimpleNamespace(
                    itemAt=Mock(return_value=SimpleNamespace(row=lambda: 0)),
                    viewport=lambda: SimpleNamespace(mapToGlobal=lambda pos: pos),
                ),
                ensure_context_row_selected=Mock(),
                selected_stock_infos=Mock(return_value=[target]),
                selected_operation_mode_set=Mock(return_value=set()),
                emergency_stop_selected_auto_trade_stocks=Mock(),
                release_selected_emergency_stopped_auto_trade_stocks=Mock(),
            )

            with patch.object(gui_auto_trade_context_menu, "QMenu", _Menu):
                gui_auto_trade_context_menu.show_auto_trade_stock_context_menu(
                    window,
                    object(),
                )

        window.ensure_context_row_selected.assert_called_once_with(0)
        window.emergency_stop_selected_auto_trade_stocks.assert_called_once_with()
        window.release_selected_emergency_stopped_auto_trade_stocks.assert_not_called()

    def test_stock_context_menu_has_no_emergency_release_action(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "stocks" / "111111_Test"
            stock_dir.mkdir(parents=True)
            (stock_dir / "state.json").write_text(
                json.dumps({"status": "EMERGENCY_STOPPED"}),
                encoding="utf-8",
            )

            class _Action:
                def __init__(self, text: str) -> None:
                    self.text = text
                    self.enabled = True

                def setEnabled(self, enabled: bool) -> None:
                    self.enabled = bool(enabled)

                def setText(self, text: str) -> None:
                    self.text = text

                def setIcon(self, _icon) -> None:
                    pass

                def setProperty(self, _name: str, _value: object) -> None:
                    pass

            class _Menu:
                chosen_action = None

                def __init__(self, _parent=None) -> None:
                    self.actions: list[_Action] = []

                def setToolTipsVisible(self, _visible: bool) -> None:
                    pass

                def addAction(self, text: str) -> _Action:
                    action = _Action(text)
                    self.actions.append(action)
                    if text == "정지해제":
                        _Menu.chosen_action = action
                    return action

                def addMenu(self, _text: str):
                    return _Menu()

                def addSeparator(self) -> None:
                    pass

                def setEnabled(self, _enabled: bool) -> None:
                    pass

                def exec_(self, _pos):
                    return _Menu.chosen_action

            window = SimpleNamespace(
                stock_table=SimpleNamespace(
                    itemAt=Mock(return_value=SimpleNamespace(row=lambda: 0)),
                    viewport=lambda: SimpleNamespace(mapToGlobal=lambda pos: pos),
                ),
                ensure_context_row_selected=Mock(),
                selected_stock_infos=Mock(return_value=[(stock_dir, "111111", "Test")]),
                selected_operation_mode_set=Mock(return_value=set()),
                emergency_stop_selected_auto_trade_stocks=Mock(),
                release_selected_emergency_stopped_auto_trade_stocks=Mock(),
            )

            with patch.object(gui_auto_trade_context_menu, "QMenu", _Menu):
                gui_auto_trade_context_menu.show_auto_trade_stock_context_menu(
                    window,
                    object(),
                )

        window.emergency_stop_selected_auto_trade_stocks.assert_not_called()
        window.release_selected_emergency_stopped_auto_trade_stocks.assert_not_called()

    def test_stock_context_menu_emergency_actions_follow_selection_state(self) -> None:
        class _Action:
            def __init__(self, text: str) -> None:
                self.text = text
                self.enabled = True

            def setEnabled(self, enabled: bool) -> None:
                self.enabled = bool(enabled)

            def setText(self, text: str) -> None:
                self.text = text

            def setIcon(self, _icon) -> None:
                pass

            def setProperty(self, _name: str, _value: object) -> None:
                pass

        class _Menu:
            action_texts: list[str] = []

            def __init__(self, _parent=None) -> None:
                pass

            def setToolTipsVisible(self, _visible: bool) -> None:
                pass

            def addAction(self, text: str) -> _Action:
                _Menu.action_texts.append(text)
                return _Action(text)

            def addMenu(self, _text: str):
                return _Menu()

            def addSeparator(self) -> None:
                pass

            def setEnabled(self, _enabled: bool) -> None:
                pass

            def exec_(self, _pos):
                return None

        with TemporaryDirectory() as temp:
            normal_dir = Path(temp) / "stocks" / "111111_Normal"
            emergency_dir = Path(temp) / "stocks" / "222222_Emergency"
            normal_dir.mkdir(parents=True)
            emergency_dir.mkdir(parents=True)
            (normal_dir / "state.json").write_text(
                json.dumps({"status": "RUNNING"}),
                encoding="utf-8",
            )
            (emergency_dir / "state.json").write_text(
                json.dumps({"status": "EMERGENCY_STOPPED"}),
                encoding="utf-8",
            )

            cases = (
                ([(normal_dir, "111111", "Normal")], True, False),
                ([(emergency_dir, "222222", "Emergency")], False, False),
                ([(normal_dir, "111111", "Normal"), (emergency_dir, "222222", "Emergency")], True, False),
            )
            for selected, expect_stop, expect_release in cases:
                with self.subTest(selected=len(selected), stop=expect_stop, release=expect_release):
                    _Menu.action_texts = []
                    window = SimpleNamespace(
                        stock_table=SimpleNamespace(
                            itemAt=Mock(return_value=None),
                            viewport=lambda: SimpleNamespace(mapToGlobal=lambda pos: pos),
                        ),
                        ensure_context_row_selected=Mock(),
                        selected_stock_infos=Mock(return_value=selected),
                        selected_operation_mode_set=Mock(return_value=set()),
                        emergency_stop_selected_auto_trade_stocks=Mock(),
                        release_selected_emergency_stopped_auto_trade_stocks=Mock(),
                    )

                    with patch.object(gui_auto_trade_context_menu, "QMenu", _Menu):
                        gui_auto_trade_context_menu.show_auto_trade_stock_context_menu(
                            window,
                            object(),
                        )

                    self.assertEqual(expect_stop, "검토정지" in _Menu.action_texts)
                    self.assertEqual(expect_release, "정지해제" in _Menu.action_texts)

    def test_stock_context_menu_stock_register_visibility_follows_routine_scope(self) -> None:
        class _Action:
            def __init__(self, text: str) -> None:
                self.text = text
                self.enabled = True

            def setEnabled(self, enabled: bool) -> None:
                self.enabled = bool(enabled)

            def setText(self, text: str) -> None:
                self.text = text

            def setIcon(self, _icon) -> None:
                pass

            def setProperty(self, _name: str, _value: object) -> None:
                pass

        class _Menu:
            action_texts: list[str] = []
            chosen_action = None

            def __init__(self, _parent=None) -> None:
                pass

            def setToolTipsVisible(self, _visible: bool) -> None:
                pass

            def addAction(self, text: str) -> _Action:
                action = _Action(text)
                _Menu.action_texts.append(text)
                if text == "종목등록":
                    _Menu.chosen_action = action
                return action

            def addMenu(self, _text: str):
                return _Menu()

            def addSeparator(self) -> None:
                pass

            def setEnabled(self, _enabled: bool) -> None:
                pass

            def exec_(self, _pos):
                return None

        cases = (
            (True, None, False),
            (False, {"row_kind": "definition"}, False),
            (
                False,
                {
                    "row_kind": "instance",
                    "definition_id": "indicator_follow",
                    "definition_name": "지표추종매매",
                    "instance_id": "inst-a",
                    "instance_name": "A 인스턴스",
                },
                True,
            ),
            (
                False,
                {
                    "row_kind": "stock",
                    "definition_id": "indicator_follow",
                    "definition_name": "지표추종매매",
                    "instance_id": "inst-a",
                    "instance_name": "A 인스턴스",
                    "stock_code": "005930",
                },
                True,
            ),
        )
        for all_scope, metadata, expected_visible in cases:
            with self.subTest(all_scope=all_scope, metadata=metadata):
                _Menu.action_texts = []
                window = SimpleNamespace(
                    _all_stocks_scope_active=all_scope,
                    stock_table=SimpleNamespace(
                        itemAt=Mock(return_value=None),
                        viewport=lambda: SimpleNamespace(mapToGlobal=lambda pos: pos),
                    ),
                    ensure_context_row_selected=Mock(),
                    selected_stock_infos=Mock(return_value=[]),
                    selected_operation_mode_set=Mock(return_value=set()),
                    current_selected_routine_row_metadata=Mock(return_value=metadata),
                    emergency_stop_selected_auto_trade_stocks=Mock(),
                    release_selected_emergency_stopped_auto_trade_stocks=Mock(),
                    open_stock_register_window=Mock(),
                    open_instance_stock_search_register_window=Mock(),
                )

                with patch.object(gui_auto_trade_context_menu, "QMenu", _Menu):
                    gui_auto_trade_context_menu.show_auto_trade_stock_context_menu(
                        window,
                        object(),
                    )

                self.assertEqual(expected_visible, "종목등록" in _Menu.action_texts)

    def test_stock_context_menu_stock_register_dispatches_instance_search_dialog(self) -> None:
        class _Action:
            def __init__(self, text: str) -> None:
                self.text = text
                self.enabled = True

            def setEnabled(self, enabled: bool) -> None:
                self.enabled = bool(enabled)

            def setText(self, text: str) -> None:
                self.text = text

            def setIcon(self, _icon) -> None:
                pass

            def setProperty(self, _name: str, _value: object) -> None:
                pass

        class _Menu:
            chosen_action = None

            def __init__(self, _parent=None) -> None:
                pass

            def setToolTipsVisible(self, _visible: bool) -> None:
                pass

            def addAction(self, text: str) -> _Action:
                action = _Action(text)
                if text == "종목등록":
                    _Menu.chosen_action = action
                return action

            def addMenu(self, _text: str):
                return _Menu()

            def addSeparator(self) -> None:
                pass

            def setEnabled(self, _enabled: bool) -> None:
                pass

            def exec_(self, _pos):
                return _Menu.chosen_action

        for row_kind in ("instance", "stock"):
            with self.subTest(row_kind=row_kind):
                metadata = {
                    "row_kind": row_kind,
                    "definition_id": "indicator_follow",
                    "definition_name": "지표추종매매",
                    "instance_id": "inst-a",
                    "instance_name": "A 인스턴스",
                    "stock_code": "005930",
                }
                _Menu.chosen_action = None
                window = SimpleNamespace(
                    _all_stocks_scope_active=False,
                    stock_table=SimpleNamespace(
                        itemAt=Mock(return_value=None),
                        viewport=lambda: SimpleNamespace(mapToGlobal=lambda pos: pos),
                    ),
                    ensure_context_row_selected=Mock(),
                    selected_stock_infos=Mock(return_value=[]),
                    selected_operation_mode_set=Mock(return_value=set()),
                    current_selected_routine_row_metadata=Mock(return_value=metadata),
                    emergency_stop_selected_auto_trade_stocks=Mock(),
                    release_selected_emergency_stopped_auto_trade_stocks=Mock(),
                    open_stock_register_window=Mock(),
                    open_instance_stock_search_register_window=Mock(),
                )

                with patch.object(gui_auto_trade_context_menu, "QMenu", _Menu):
                    gui_auto_trade_context_menu.show_auto_trade_stock_context_menu(
                        window,
                        object(),
                    )

                window.open_stock_register_window.assert_not_called()
                window.open_instance_stock_search_register_window.assert_called_once_with(
                    {
                        "row_kind": "instance",
                        "definition_id": "indicator_follow",
                        "definition_name": "지표추종매매",
                        "instance_id": "inst-a",
                        "instance_name": "A 인스턴스",
                    }
                )

    def test_stock_context_menu_running_view_adds_set_exclusion_above_unregister(self) -> None:
        class _Action:
            def __init__(self, text: str) -> None:
                self.text = text
                self.enabled = True

            def setEnabled(self, enabled: bool) -> None:
                self.enabled = bool(enabled)

            def setText(self, text: str) -> None:
                self.text = text

            def setIcon(self, _icon) -> None:
                pass

            def setProperty(self, _name: str, _value: object) -> None:
                pass

        class _Menu:
            action_texts: list[str] = []
            chosen_action = None

            def __init__(self, _parent=None) -> None:
                pass

            def setToolTipsVisible(self, _visible: bool) -> None:
                pass

            def addAction(self, text: str) -> _Action:
                action = _Action(text)
                _Menu.action_texts.append(text)
                return action

            def addMenu(self, _text: str):
                return _Menu()

            def addSeparator(self) -> None:
                pass

            def setEnabled(self, _enabled: bool) -> None:
                pass

            def exec_(self, _pos):
                return None

        window = SimpleNamespace(
            _stock_status_filter="running",
            stock_table=SimpleNamespace(
                itemAt=Mock(return_value=None),
                viewport=lambda: SimpleNamespace(mapToGlobal=lambda pos: pos),
            ),
            ensure_context_row_selected=Mock(),
            selected_stock_infos=Mock(return_value=[(Path("stocks/111111_Test"), "111111", "Test")]),
            selected_operation_mode_set=Mock(return_value=set()),
            current_selected_routine_row_metadata=Mock(return_value=None),
            emergency_stop_selected_auto_trade_stocks=Mock(),
            release_selected_emergency_stopped_auto_trade_stocks=Mock(),
            set_selected_stock_operation_exclusions=Mock(),
            clear_selected_stock_operation_exclusions=Mock(),
            unregister_selected_auto_trade_stocks=Mock(),
        )

        with patch.object(gui_auto_trade_context_menu, "QMenu", _Menu):
            gui_auto_trade_context_menu.show_auto_trade_stock_context_menu(
                window,
                object(),
            )

        self.assertIn("운영제외", _Menu.action_texts)
        self.assertNotIn("제외해제", _Menu.action_texts)
        self.assertLess(
            _Menu.action_texts.index("운영제외"),
            _Menu.action_texts.index("등록해제"),
        )

    def test_stock_context_menu_disables_set_exclusion_for_current_running_target(
        self,
    ) -> None:
        class _Action:
            def __init__(self, text: str) -> None:
                self.text = text
                self.enabled = True

            def setEnabled(self, enabled: bool) -> None:
                self.enabled = bool(enabled)

            def setText(self, text: str) -> None:
                self.text = text

            def setIcon(self, _icon) -> None:
                pass

            def setProperty(self, _name: str, _value: object) -> None:
                pass

        class _Menu:
            set_exclusion_action = None

            def __init__(self, _parent=None) -> None:
                pass

            def setToolTipsVisible(self, _visible: bool) -> None:
                pass

            def addAction(self, text: str) -> _Action:
                action = _Action(text)
                if text == "운영제외":
                    _Menu.set_exclusion_action = action
                return action

            def addMenu(self, _text: str):
                return _Menu()

            def addSeparator(self) -> None:
                pass

            def setEnabled(self, _enabled: bool) -> None:
                pass

            def exec_(self, _pos):
                return None

        target = (Path("stocks/111111_Test"), "111111", "Test")
        window = SimpleNamespace(
            _stock_status_filter="running",
            stock_table=SimpleNamespace(
                itemAt=Mock(return_value=None),
                viewport=lambda: SimpleNamespace(mapToGlobal=lambda pos: pos),
            ),
            ensure_context_row_selected=Mock(),
            selected_stock_infos=Mock(return_value=[target]),
            selected_operation_mode_set=Mock(return_value=set()),
            current_selected_routine_row_metadata=Mock(return_value=None),
            emergency_stop_selected_auto_trade_stocks=Mock(),
            set_selected_stock_operation_exclusions=Mock(),
            unregister_selected_auto_trade_stocks=Mock(),
        )

        with (
            patch.object(gui_auto_trade_context_menu, "QMenu", _Menu),
            patch.object(
                gui_auto_trade_context_menu,
                "inspect_auto_trade_operation_exclusion_availability",
                return_value=SimpleNamespace(
                    allowed=False,
                    reason_code="CURRENTLY_RUNNING",
                ),
            ),
        ):
            gui_auto_trade_context_menu.show_auto_trade_stock_context_menu(
                window,
                object(),
            )

        self.assertIsNotNone(_Menu.set_exclusion_action)
        self.assertFalse(_Menu.set_exclusion_action.enabled)
        window.set_selected_stock_operation_exclusions.assert_not_called()

    def test_stock_context_menu_running_view_dispatches_set_exclusion(self) -> None:
        class _Action:
            def __init__(self, text: str) -> None:
                self.text = text
                self.enabled = True

            def setEnabled(self, enabled: bool) -> None:
                self.enabled = bool(enabled)

            def setText(self, text: str) -> None:
                self.text = text

            def setIcon(self, _icon) -> None:
                pass

            def setProperty(self, _name: str, _value: object) -> None:
                pass

        class _Menu:
            chosen_action = None

            def __init__(self, _parent=None) -> None:
                pass

            def setToolTipsVisible(self, _visible: bool) -> None:
                pass

            def addAction(self, text: str) -> _Action:
                action = _Action(text)
                if text == "운영제외":
                    _Menu.chosen_action = action
                return action

            def addMenu(self, _text: str):
                return _Menu()

            def addSeparator(self) -> None:
                pass

            def setEnabled(self, _enabled: bool) -> None:
                pass

            def exec_(self, _pos):
                return _Menu.chosen_action

        with TemporaryDirectory() as temp:
            target = self._canonical_target(temp)
            window = SimpleNamespace(
                _stock_status_filter="running",
                stock_table=SimpleNamespace(
                    itemAt=Mock(return_value=SimpleNamespace(row=lambda: 0)),
                    viewport=lambda: SimpleNamespace(mapToGlobal=lambda pos: pos),
                ),
                ensure_context_row_selected=Mock(),
                selected_stock_infos=Mock(return_value=[target]),
                selected_operation_mode_set=Mock(return_value=set()),
                current_selected_routine_row_metadata=Mock(return_value=None),
                emergency_stop_selected_auto_trade_stocks=Mock(),
                release_selected_emergency_stopped_auto_trade_stocks=Mock(),
                set_selected_stock_operation_exclusions=Mock(),
                unregister_selected_auto_trade_stocks=Mock(),
            )

            with patch.object(gui_auto_trade_context_menu, "QMenu", _Menu):
                gui_auto_trade_context_menu.show_auto_trade_stock_context_menu(
                    window,
                    object(),
                )

        window.ensure_context_row_selected.assert_called_once_with(0)
        window.set_selected_stock_operation_exclusions.assert_called_once_with()
        window.unregister_selected_auto_trade_stocks.assert_not_called()

    def test_stock_context_menu_running_view_disables_set_exclusion_without_selection(self) -> None:
        class _Action:
            def __init__(self, text: str) -> None:
                self.text = text
                self.enabled = True

            def setEnabled(self, enabled: bool) -> None:
                self.enabled = bool(enabled)

            def setText(self, text: str) -> None:
                self.text = text

            def setIcon(self, _icon) -> None:
                pass

            def setProperty(self, _name: str, _value: object) -> None:
                pass

        class _Menu:
            set_exclusion_action = None

            def __init__(self, _parent=None) -> None:
                pass

            def setToolTipsVisible(self, _visible: bool) -> None:
                pass

            def addAction(self, text: str) -> _Action:
                action = _Action(text)
                if text == "운영제외":
                    _Menu.set_exclusion_action = action
                return action

            def addMenu(self, _text: str):
                return _Menu()

            def addSeparator(self) -> None:
                pass

            def setEnabled(self, _enabled: bool) -> None:
                pass

            def exec_(self, _pos):
                return None

        window = SimpleNamespace(
            _stock_status_filter="running",
            stock_table=SimpleNamespace(
                itemAt=Mock(return_value=None),
                viewport=lambda: SimpleNamespace(mapToGlobal=lambda pos: pos),
            ),
            ensure_context_row_selected=Mock(),
            selected_stock_infos=Mock(return_value=[]),
            selected_operation_mode_set=Mock(return_value=set()),
            current_selected_routine_row_metadata=Mock(return_value=None),
            emergency_stop_selected_auto_trade_stocks=Mock(),
            release_selected_emergency_stopped_auto_trade_stocks=Mock(),
            set_selected_stock_operation_exclusions=Mock(),
            unregister_selected_auto_trade_stocks=Mock(),
        )

        with patch.object(gui_auto_trade_context_menu, "QMenu", _Menu):
            gui_auto_trade_context_menu.show_auto_trade_stock_context_menu(
                window,
                object(),
            )

        self.assertIsNotNone(_Menu.set_exclusion_action)
        self.assertFalse(_Menu.set_exclusion_action.enabled)

    def test_stock_context_menu_excluded_view_replaces_unregister_with_clear_exclusion(self) -> None:
        class _Action:
            def __init__(self, text: str) -> None:
                self.text = text
                self.enabled = True

            def setEnabled(self, enabled: bool) -> None:
                self.enabled = bool(enabled)

            def setText(self, text: str) -> None:
                self.text = text

            def setIcon(self, _icon) -> None:
                pass

            def setProperty(self, _name: str, _value: object) -> None:
                pass

        class _Menu:
            action_texts: list[str] = []

            def __init__(self, _parent=None) -> None:
                pass

            def setToolTipsVisible(self, _visible: bool) -> None:
                pass

            def addAction(self, text: str) -> _Action:
                _Menu.action_texts.append(text)
                return _Action(text)

            def addMenu(self, _text: str):
                return _Menu()

            def addSeparator(self) -> None:
                pass

            def setEnabled(self, _enabled: bool) -> None:
                pass

            def exec_(self, _pos):
                return None

        window = SimpleNamespace(
            _all_stocks_scope_active=False,
            _stock_status_filter="excluded",
            stock_table=SimpleNamespace(
                itemAt=Mock(return_value=None),
                viewport=lambda: SimpleNamespace(mapToGlobal=lambda pos: pos),
            ),
            ensure_context_row_selected=Mock(),
            selected_stock_infos=Mock(return_value=[(Path("stocks/111111_Test"), "111111", "Test")]),
            selected_operation_mode_set=Mock(return_value=set()),
            current_selected_routine_row_metadata=Mock(
                return_value={
                    "row_kind": "instance",
                    "definition_id": "indicator_follow",
                    "definition_name": "지표추종매매",
                    "instance_id": "inst-a",
                    "instance_name": "A 인스턴스",
                }
            ),
            emergency_stop_selected_auto_trade_stocks=Mock(),
            release_selected_emergency_stopped_auto_trade_stocks=Mock(),
            clear_selected_stock_operation_exclusions=Mock(),
            unregister_selected_auto_trade_stocks=Mock(),
            open_instance_stock_search_register_window=Mock(),
        )

        with patch.object(gui_auto_trade_context_menu, "QMenu", _Menu):
            gui_auto_trade_context_menu.show_auto_trade_stock_context_menu(
                window,
                object(),
            )

        self.assertIn("제외해제", _Menu.action_texts)
        self.assertNotIn("등록해제", _Menu.action_texts)
        self.assertNotIn("종목등록", _Menu.action_texts)

    def test_stock_context_menu_excluded_view_disables_clear_exclusion_without_selection(self) -> None:
        class _Action:
            def __init__(self, text: str) -> None:
                self.text = text
                self.enabled = True

            def setEnabled(self, enabled: bool) -> None:
                self.enabled = bool(enabled)

            def setText(self, text: str) -> None:
                self.text = text

            def setIcon(self, _icon) -> None:
                pass

            def setProperty(self, _name: str, _value: object) -> None:
                pass

        class _Menu:
            clear_exclusion_action = None

            def __init__(self, _parent=None) -> None:
                pass

            def setToolTipsVisible(self, _visible: bool) -> None:
                pass

            def addAction(self, text: str) -> _Action:
                action = _Action(text)
                if text == "제외해제":
                    _Menu.clear_exclusion_action = action
                return action

            def addMenu(self, _text: str):
                return _Menu()

            def addSeparator(self) -> None:
                pass

            def setEnabled(self, _enabled: bool) -> None:
                pass

            def exec_(self, _pos):
                return None

        window = SimpleNamespace(
            _stock_status_filter="excluded",
            stock_table=SimpleNamespace(
                itemAt=Mock(return_value=None),
                viewport=lambda: SimpleNamespace(mapToGlobal=lambda pos: pos),
            ),
            ensure_context_row_selected=Mock(),
            selected_stock_infos=Mock(return_value=[]),
            selected_operation_mode_set=Mock(return_value=set()),
            current_selected_routine_row_metadata=Mock(return_value=None),
            emergency_stop_selected_auto_trade_stocks=Mock(),
            release_selected_emergency_stopped_auto_trade_stocks=Mock(),
            clear_selected_stock_operation_exclusions=Mock(),
            unregister_selected_auto_trade_stocks=Mock(),
        )

        with patch.object(gui_auto_trade_context_menu, "QMenu", _Menu):
            gui_auto_trade_context_menu.show_auto_trade_stock_context_menu(
                window,
                object(),
            )

        self.assertIsNotNone(_Menu.clear_exclusion_action)
        self.assertFalse(_Menu.clear_exclusion_action.enabled)

    def test_stock_context_menu_excluded_view_dispatches_clear_exclusion(self) -> None:
        class _Action:
            def __init__(self, text: str) -> None:
                self.text = text
                self.enabled = True

            def setEnabled(self, enabled: bool) -> None:
                self.enabled = bool(enabled)

            def setText(self, text: str) -> None:
                self.text = text

            def setIcon(self, _icon) -> None:
                pass

            def setProperty(self, _name: str, _value: object) -> None:
                pass

        class _Menu:
            chosen_action = None

            def __init__(self, _parent=None) -> None:
                pass

            def setToolTipsVisible(self, _visible: bool) -> None:
                pass

            def addAction(self, text: str) -> _Action:
                action = _Action(text)
                if text == "제외해제":
                    _Menu.chosen_action = action
                return action

            def addMenu(self, _text: str):
                return _Menu()

            def addSeparator(self) -> None:
                pass

            def setEnabled(self, _enabled: bool) -> None:
                pass

            def exec_(self, _pos):
                return _Menu.chosen_action

        with TemporaryDirectory() as temp:
            target = self._canonical_target(temp, excluded=True)
            window = SimpleNamespace(
                _stock_status_filter="excluded",
                stock_table=SimpleNamespace(
                    itemAt=Mock(return_value=SimpleNamespace(row=lambda: 0)),
                    viewport=lambda: SimpleNamespace(mapToGlobal=lambda pos: pos),
                ),
                ensure_context_row_selected=Mock(),
                selected_stock_infos=Mock(return_value=[target]),
                selected_operation_mode_set=Mock(return_value=set()),
                current_selected_routine_row_metadata=Mock(return_value=None),
                emergency_stop_selected_auto_trade_stocks=Mock(),
                release_selected_emergency_stopped_auto_trade_stocks=Mock(),
                clear_selected_stock_operation_exclusions=Mock(),
                unregister_selected_auto_trade_stocks=Mock(),
            )

            with patch.object(gui_auto_trade_context_menu, "QMenu", _Menu):
                gui_auto_trade_context_menu.show_auto_trade_stock_context_menu(
                    window,
                    object(),
                )

        window.ensure_context_row_selected.assert_called_once_with(0)
        window.clear_selected_stock_operation_exclusions.assert_called_once_with()
        window.unregister_selected_auto_trade_stocks.assert_not_called()

    def _apply_routine_assignment(
        self,
        root: Path,
        *,
        running: bool,
        code: str = "222222",
        name: str = "Fresh",
    ) -> Path:
        routine_dir = root / "routines" / "def_running"
        routine_dir.mkdir(parents=True, exist_ok=True)
        (routine_dir / "routine.py").write_text("", encoding="utf-8")
        (routine_dir / "routine.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "definition_id": "def_running",
                    "name": "Routine",
                    "entry_file": "routine.py",
                    "rules_file": "rules.json",
                    "enabled": True,
                }
            ),
            encoding="utf-8",
        )
        group_dir = root / "groups" / self.ASSIGNMENT_GROUP_ID
        group_dir.mkdir(parents=True, exist_ok=True)
        (group_dir / "group.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "group_id": self.ASSIGNMENT_GROUP_ID,
                    "definition_id": "def_running",
                    "base_name": "Running",
                    "display_name": "Running",
                    "slot": 0,
                    "created_at": "2026-08-30T09:00:00+09:00",
                }
            ),
            encoding="utf-8",
        )
        (root / "groups" / "registry.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "mode": "logical",
                    "group_ids": [self.ASSIGNMENT_GROUP_ID],
                    "cutover_at": "2026-08-30T09:00:00+09:00",
                }
            ),
            encoding="utf-8",
        )
        instance_dir = root / "routine_instances" / self.ASSIGNMENT_INSTANCE_ID
        instance_dir.mkdir(parents=True, exist_ok=True)
        (instance_dir / "instance.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "instance_id": self.ASSIGNMENT_INSTANCE_ID,
                    "definition_id": "def_running",
                    "display_name": "Running",
                    "enabled": False,
                    "buy_limit_enabled": False,
                    "buy_limit_amount": None,
                    "rules_file": "rules.json",
                    "created_at": "2026-08-30T09:00:00+09:00",
                    "updated_at": "2026-08-30T09:00:00+09:00",
                    "group_id": self.ASSIGNMENT_GROUP_ID,
                }
            ),
            encoding="utf-8",
        )
        (instance_dir / "rules.json").write_text("{}", encoding="utf-8")
        repository = StockRepository(root)
        stock_dir = repository.ensure_stock_folder(code, name, routine="")
        auto_trade_setting = SimpleNamespace(
            running_registered_operation_targets=Mock(
                return_value=[(Path("stocks/000001_Run"), "000001", "Run")]
                if running
                else []
            ),
            refresh_all=Mock(),
        )
        window = SimpleNamespace(
            parent=lambda: auto_trade_setting,
        )

        assign_stock_routine(
            root,
            code,
            name,
            instance_id=self.ASSIGNMENT_INSTANCE_ID,
            instance_name="Running",
            definition_id="def_running",
            routine_type="Routine",
            stock_repository=repository,
        )
        return stock_dir

    def test_running_global_operation_first_assignment_preserves_waiting(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = self._apply_routine_assignment(Path(temp), running=True)
            config = read_json_dict(stock_dir / "config.json")
            window = SimpleNamespace()
            window.registered_operation_targets = lambda: [
                (stock_dir, "222222", "Fresh")
            ]
            window.registered_operation_start_targets = lambda: (
                AutoTradeSettingWindow.registered_operation_start_targets(window)
            )
            start_targets = window.registered_operation_start_targets()

        self.assertNotIn(OPERATION_EXCLUDED_CONFIG_KEY, config)
        self.assertEqual(self.ASSIGNMENT_INSTANCE_ID, config["assigned_routine_instance_id"])
        self.assertEqual(1, len(start_targets))

    def test_before_global_operation_new_assignment_keeps_existing_default(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = self._apply_routine_assignment(Path(temp), running=False)
            config = read_json_dict(stock_dir / "config.json")

        self.assertNotIn(OPERATION_EXCLUDED_CONFIG_KEY, config)
        self.assertEqual(self.ASSIGNMENT_INSTANCE_ID, config["assigned_routine_instance_id"])

    def test_running_assignment_does_not_overwrite_existing_operation_excluded_value(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            repository = StockRepository(root)
            stock_dir = repository.ensure_stock_folder("222222", "Fresh", routine="")
            config_path = stock_dir / "config.json"
            config = read_json_dict(config_path)
            config[OPERATION_EXCLUDED_CONFIG_KEY] = False
            config_path.write_text(
                json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            stock_dir = self._apply_routine_assignment(root, running=True)
            config = read_json_dict(stock_dir / "config.json")

        self.assertFalse(config[OPERATION_EXCLUDED_CONFIG_KEY])

if __name__ == "__main__":
    unittest.main()
