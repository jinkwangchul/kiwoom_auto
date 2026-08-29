import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PyQt5.QtWidgets import QApplication

import gui_auto_trade_context_menu as context_menu


class _Action:
    def __init__(self, text: str) -> None:
        self.label = text
        self.enabled = True

    def setEnabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)

    def setText(self, text: str) -> None:
        self.label = text

    def setIcon(self, _icon) -> None:
        pass

    def setProperty(self, _name: str, _value: object) -> None:
        pass


class _Menu:
    latest_root = None
    chosen_label = ""

    def __init__(self, _parent=None, *, label: str = "", root=None) -> None:
        self.label = label
        self.enabled = True
        self.entries = []
        self.root = root or self
        if root is None:
            _Menu.latest_root = self

    def setToolTipsVisible(self, _visible: bool) -> None:
        pass

    def addAction(self, text: str) -> _Action:
        action = _Action(text)
        self.entries.append(action)
        return action

    def addMenu(self, text: str):
        submenu = _Menu(label=text, root=self.root)
        self.entries.append(submenu)
        return submenu

    def addSeparator(self) -> None:
        pass

    def setEnabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)

    def setToolTip(self, _text: str) -> None:
        pass

    def exec_(self, _pos):
        return self._find_action(self.root, _Menu.chosen_label)

    @classmethod
    def _find_action(cls, menu, label: str):
        if not label:
            return None
        for entry in menu.entries:
            if isinstance(entry, _Action) and entry.label == label:
                return entry
            if isinstance(entry, _Menu):
                found = cls._find_action(entry, label)
                if found is not None:
                    return found
        return None

    def top_entries(self) -> dict[str, object]:
        return {entry.label: entry for entry in self.entries}


class ReviewManagedStockContextMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _render(
        self,
        mode: str,
        *,
        review: bool,
        chosen_label: str = "",
        operation_excluded: bool = False,
        scheduled_excluded_management: bool = False,
    ):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        stock_dir = Path(temp.name) / "111111_Test"
        stock_dir.mkdir()
        (stock_dir / "state.json").write_text(
            json.dumps(
                {
                    "status": "REVIEW_REQUIRED" if review else "STOPPED",
                    "trade_enabled": False,
                }
            ),
            encoding="utf-8",
        )
        target = (stock_dir, "111111", "Test")
        callbacks = context_menu.StockContextMenuCallbacks(
            select_all=Mock(),
            clear_selection=Mock(),
            early_close=Mock(),
            early_close_profit_loss=Mock(),
            early_close_cancel=Mock(),
            individual_liquidation=Mock(),
            open_charts=Mock(),
            start=Mock(),
            emergency_stop=Mock(),
            unregister=Mock(),
            stock_register=Mock(),
            time_change=Mock(),
            time_reset=Mock(),
            ats_state=Mock(return_value={}),
            ats_toggle=Mock(),
            ats_execution_method_state=Mock(
                return_value={
                    "ok": True,
                    "execution_method": "ROUTINE",
                    "mixed": False,
                }
            ),
            ats_execution_method_set=Mock(),
            ats_liquidation_available=Mock(return_value=False),
            ats_liquidation=Mock(),
            set_operation_exclusion=Mock(),
            clear_operation_exclusion=Mock(),
            trade_permission_label=Mock(
                return_value=(
                    "실주문 전환" if operation_excluded else "감시전용 전환"
                )
            ),
            toggle_trade_permission=Mock(),
        )
        parent = SimpleNamespace()
        _Menu.chosen_label = chosen_label
        with (
            patch.object(context_menu, "QMenu", _Menu),
            patch.object(
                context_menu,
                "auto_trade_operation_exclusion_mutation_decision",
                return_value={"allowed": True, "current_running": False},
            ),
            patch.object(
                context_menu,
                "manual_ats_visible_session_keys",
                return_value=("after_hours",),
            ),
            patch.object(
                context_menu,
                "manual_ats_session_labels",
                return_value={"after_hours": "장후"},
            ),
            patch.object(context_menu, "append_production_event"),
        ):
            context_menu.show_monitor_stock_context_menu(
                parent,
                object(),
                has_selection=True,
                callbacks=callbacks,
                selected_modes={mode},
                operation_excluded=operation_excluded,
                operation_exclusion_action=(
                    "clear" if operation_excluded else "set"
                ),
                stock_register_enabled=True,
                selected_targets=[target],
                scheduled_excluded_management=scheduled_excluded_management,
            )
        return _Menu.latest_root, callbacks

    def test_scheduled_review_keeps_only_case_a_management_actions_enabled(self) -> None:
        review_menu, _callbacks = self._render("SCHEDULED", review=True)
        normal_menu, _callbacks = self._render("SCHEDULED", review=False)
        review = review_menu.top_entries()
        normal = normal_menu.top_entries()

        self.assertEqual(list(normal), list(review))
        for label in ("전체선택", "선택해제", "시간변경", "변경리셋", "등록해제"):
            self.assertTrue(review[label].enabled, label)
        for label in (
            "운영시작",
            "검토정지",
            "운영제외",
            "감시전용 전환",
            "조기마감",
            "개별청산",
            "종목등록",
            "간이차트",
        ):
            self.assertFalse(review[label].enabled, label)
        self.assertNotIn("ATS설정", review)

    def test_continuous_review_keeps_only_case_b_management_actions_enabled(self) -> None:
        review_menu, _callbacks = self._render("CONTINUOUS", review=True)
        normal_menu, _callbacks = self._render("CONTINUOUS", review=False)
        review = review_menu.top_entries()
        normal = normal_menu.top_entries()

        self.assertEqual(list(normal), list(review))
        for label in ("전체선택", "선택해제", "ATS설정", "등록해제"):
            self.assertTrue(review[label].enabled, label)
        for label in (
            "운영시작",
            "검토정지",
            "운영제외",
            "감시전용 전환",
            "조기마감",
            "개별청산",
            "종목등록",
            "간이차트",
        ):
            self.assertFalse(review[label].enabled, label)
        self.assertNotIn("시간변경", review)
        self.assertNotIn("변경리셋", review)

    def test_non_review_context_menu_keeps_existing_actions_enabled(self) -> None:
        menu, _callbacks = self._render("SCHEDULED", review=False)
        entries = menu.top_entries()

        for label in (
            "운영시작",
            "검토정지",
            "운영제외",
            "감시전용 전환",
            "조기마감",
            "개별청산",
            "시간변경",
            "변경리셋",
            "종목등록",
            "등록해제",
            "간이차트",
        ):
            self.assertTrue(entries[label].enabled, label)

    def test_scheduled_excluded_management_keeps_exact_requested_actions_enabled(self) -> None:
        menu, _callbacks = self._render(
            "SCHEDULED",
            review=False,
            operation_excluded=True,
            scheduled_excluded_management=True,
        )
        entries = menu.top_entries()

        self.assertEqual(
            [
                "운영시작",
                "검토정지",
                "전체선택",
                "선택해제",
                "제외해제",
                "실주문 전환",
                "조기마감",
                "개별청산",
                "시간변경",
                "변경리셋",
                "종목등록",
                "등록해제",
                "간이차트",
            ],
            list(entries),
        )
        for label in (
            "운영시작",
            "전체선택",
            "선택해제",
            "제외해제",
            "실주문 전환",
            "시간변경",
            "변경리셋",
            "등록해제",
            "간이차트",
        ):
            self.assertTrue(entries[label].enabled, label)
        for label in ("검토정지", "조기마감", "개별청산", "종목등록"):
            self.assertFalse(entries[label].enabled, label)

    def test_scheduled_excluded_management_dispatches_each_enabled_action(self) -> None:
        callback_by_label = {
            "운영시작": "start",
            "전체선택": "select_all",
            "선택해제": "clear_selection",
            "제외해제": "clear_operation_exclusion",
            "실주문 전환": "toggle_trade_permission",
            "시간변경": "time_change",
            "변경리셋": "time_reset",
            "등록해제": "unregister",
            "간이차트": "open_charts",
        }
        for label, callback_name in callback_by_label.items():
            with self.subTest(label=label):
                _menu, callbacks = self._render(
                    "SCHEDULED",
                    review=False,
                    chosen_label=label,
                    operation_excluded=True,
                    scheduled_excluded_management=True,
                )
                getattr(callbacks, callback_name).assert_called_once_with()

    def test_scheduled_excluded_management_rejects_forced_disabled_actions(self) -> None:
        for label in ("검토정지", "루틴마감", "종목등록"):
            with self.subTest(label=label):
                _menu, callbacks = self._render(
                    "SCHEDULED",
                    review=False,
                    chosen_label=label,
                    operation_excluded=True,
                    scheduled_excluded_management=True,
                )
                callbacks.emergency_stop.assert_not_called()
                callbacks.early_close.assert_not_called()
                callbacks.individual_liquidation.assert_not_called()
                callbacks.stock_register.assert_not_called()

    def test_review_dispatch_rejects_forced_disabled_actions(self) -> None:
        for label in ("운영시작", "루틴마감"):
            with self.subTest(label=label):
                _menu, callbacks = self._render(
                    "SCHEDULED",
                    review=True,
                    chosen_label=label,
                )

                callbacks.start.assert_not_called()
                callbacks.early_close.assert_not_called()
                callbacks.individual_liquidation.assert_not_called()


if __name__ == "__main__":
    unittest.main()
