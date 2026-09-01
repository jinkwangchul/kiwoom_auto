import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PyQt5.QtWidgets import QApplication

import gui_auto_trade_context_menu as context_menu
from tests.participant_owner_fixture import participant_owner


class _Action:
    def __init__(self, text: str) -> None:
        self.label = text
        self.enabled = True
        self.tooltip = ""
        self.status_tip = ""

    def setEnabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)

    def setText(self, text: str) -> None:
        self.label = text

    def setIcon(self, _icon) -> None:
        pass

    def setProperty(self, _name: str, _value: object) -> None:
        pass

    def setToolTip(self, text: str) -> None:
        self.tooltip = str(text)

    def setStatusTip(self, text: str) -> None:
        self.status_tip = str(text)


class _Menu:
    latest_root = None
    chosen_label = ""
    chosen_menu_label = ""

    def __init__(self, _parent=None, *, label: str = "", root=None) -> None:
        self.label = label
        self.enabled = True
        self.entries = []
        self.tooltip = ""
        self.status_tip = ""
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

    def setToolTip(self, text: str) -> None:
        self.tooltip = str(text)

    def setStatusTip(self, text: str) -> None:
        self.status_tip = str(text)

    def exec_(self, _pos):
        return self._find_action(
            self.root,
            _Menu.chosen_label,
            _Menu.chosen_menu_label,
        )

    @classmethod
    def _find_action(cls, menu, label: str, menu_label: str = ""):
        if not label:
            return None
        if not menu_label or menu.label == menu_label:
            for entry in menu.entries:
                if isinstance(entry, _Action) and entry.label == label:
                    return entry
        for entry in menu.entries:
            if isinstance(entry, _Menu):
                found = cls._find_action(entry, label, menu_label)
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
        chosen_menu_label: str = "",
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
                    "status": "REVIEW_REQUIRED" if review else "RUNNING",
                    "trade_enabled": not review,
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
        (stock_dir / "config.json").write_text(
            json.dumps({"operation_excluded": operation_excluded}),
            encoding="utf-8",
        )
        paths = (stock_dir / "config.json", stock_dir / "state.json")
        before_files = {path.name: path.read_bytes() for path in paths}
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
        runtime_state = {"marker": "unchanged"}
        parent = SimpleNamespace(
            runtime_state=runtime_state,
            _main_monitoring_auto_trade_operation_host=participant_owner(),
        )
        _Menu.chosen_label = chosen_label
        _Menu.chosen_menu_label = chosen_menu_label
        with (
            patch.object(context_menu, "QMenu", _Menu),
            patch.object(
                context_menu,
                "inspect_auto_trade_operation_exclusion_availability",
                return_value=SimpleNamespace(allowed=True, reason_code=""),
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
        _Menu.latest_root._test_before_files = before_files
        _Menu.latest_root._test_after_files = {
            path.name: path.read_bytes() for path in paths
        }
        _Menu.latest_root._test_runtime_state = dict(runtime_state)
        return _Menu.latest_root, callbacks

    def test_scheduled_review_keeps_only_case_a_management_actions_enabled(self) -> None:
        review_menu, _callbacks = self._render("SCHEDULED", review=True)
        normal_menu, _callbacks = self._render("SCHEDULED", review=False)
        review = review_menu.top_entries()
        normal = normal_menu.top_entries()

        self.assertEqual(list(normal), list(review))
        for label in ("전체선택", "선택해제", "시간변경", "변경리셋"):
            self.assertTrue(review[label].enabled, label)
        for label in (
            "운영시작",
            "검토정지",
            "운영제외",
            "감시전용 전환",
            "조기마감",
            "개별청산",
            "종목등록",
            "등록해제",
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
        for label in ("전체선택", "선택해제", "ATS설정"):
            self.assertTrue(review[label].enabled, label)
        for label in (
            "운영시작",
            "검토정지",
            "운영제외",
            "감시전용 전환",
            "조기마감",
            "개별청산",
            "종목등록",
            "등록해제",
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
            "시간변경",
            "변경리셋",
            "종목등록",
            "등록해제",
            "간이차트",
        ):
            self.assertTrue(entries[label].enabled, label)
        for label in ("조기마감", "개별청산"):
            self.assertFalse(entries[label].enabled, label)

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
        for label in ("운영시작", "루틴마감", "등록해제"):
            with self.subTest(label=label):
                _menu, callbacks = self._render(
                    "SCHEDULED",
                    review=True,
                    chosen_label=label,
                )

                callbacks.start.assert_not_called()
                callbacks.early_close.assert_not_called()
                callbacks.individual_liquidation.assert_not_called()
                callbacks.unregister.assert_not_called()

    def test_review_disabled_submenus_cannot_be_forced_to_dispatch(self) -> None:
        for menu_label, action_label in (
            ("조기마감", "시장가"),
            ("개별청산", "시장가"),
        ):
            with self.subTest(menu=menu_label):
                _menu, callbacks = self._render(
                    "SCHEDULED",
                    review=True,
                    chosen_label=action_label,
                    chosen_menu_label=menu_label,
                )

                callbacks.early_close.assert_not_called()
                callbacks.individual_liquidation.assert_not_called()

    def test_review_projection_preserves_canonical_reason_codes(self) -> None:
        menu, _callbacks = self._render("SCHEDULED", review=True)

        availability = menu._stock_context_availability
        self.assertTrue(availability.review_managed)
        self.assertEqual(availability.reason_for("start"), "REVIEW_REQUIRED")
        self.assertEqual(availability.reason_for("exclusion"), "REVIEW_REQUIRED")
        self.assertEqual(
            availability.reason_for("trade_permission"),
            "REVIEW_REQUIRED",
        )
        self.assertEqual(availability.reason_for("unregister"), "REVIEW_REQUIRED")

    def test_review_disabled_actions_explain_user_reason_without_changing_policy(self) -> None:
        menu, _callbacks = self._render("SCHEDULED", review=True)
        entries = menu.top_entries()

        for label in ("운영시작", "운영제외", "감시전용 전환", "등록해제", "간이차트"):
            with self.subTest(label=label):
                action = entries[label]
                self.assertFalse(action.enabled)
                self.assertEqual("검토가 필요한 종목입니다.", action.tooltip)
                self.assertEqual(action.tooltip, action.status_tip)

        self.assertTrue(entries["시간변경"].enabled)
        self.assertTrue(entries["변경리셋"].enabled)

    def test_context_menu_open_close_is_read_only(self) -> None:
        menu, callbacks = self._render("SCHEDULED", review=True)

        self.assertEqual(menu._test_before_files, menu._test_after_files)
        self.assertEqual(menu._test_runtime_state, {"marker": "unchanged"})
        callbacks.start.assert_not_called()
        callbacks.unregister.assert_not_called()


if __name__ == "__main__":
    unittest.main()
