from __future__ import annotations

import os
from types import MethodType
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import sip
from PyQt5.QtCore import QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QGridLayout,
    QGroupBox,
    QWidget,
)

from gui_auto_trade_setting_window import AutoTradeNotificationPopup
from gui_indicator_follow_routine_settings_dialog import (
    IndicatorFollowRoutineSettingsDialog,
)
from tests.qt_test_support import ensure_qapplication, flush_deferred_deletes


class F3LifecycleCleanupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = ensure_qapplication()

    def flush_deferred_deletes(self) -> None:
        flush_deferred_deletes(self.app)

    def approval_owner(self, decision_callback=None, save_callback=None) -> QDialog:
        owner = QDialog()
        box = QGroupBox(owner)
        layout = QGridLayout(box)
        owner._rule_approval_controls_box = box
        owner._rule_approval_controls_layout = layout
        owner._rule_approval_decision_widgets = {}
        owner._rule_approval_save_button = None
        owner._clear_rule_approval_controls_layout = MethodType(
            IndicatorFollowRoutineSettingsDialog._clear_rule_approval_controls_layout,
            owner,
        )
        owner._rule_candidate_risk = lambda _path: "low"
        owner._rule_candidate_note = lambda path: path
        owner._rule_candidate_display_label = lambda path: path
        owner._handle_rule_approval_decision_changed = (
            decision_callback or (lambda *_args: None)
        )
        owner._handle_rule_approval_session_save_clicked = (
            save_callback or (lambda: None)
        )
        return owner

    @staticmethod
    def approval_session() -> dict[str, object]:
        return {
            "decisions": {
                "buy.groups[0].conditions": "PENDING",
                "sell.signals.ui_preview_condition_c": "PENDING",
            },
            "candidate_types": {
                "buy.groups[0].conditions": "BUY",
                "sell.signals.ui_preview_condition_c": "SELL",
            },
        }

    @staticmethod
    def refresh_approval(owner: QDialog) -> None:
        IndicatorFollowRoutineSettingsDialog._refresh_rule_approval_controls(
            owner,
            F3LifecycleCleanupTest.approval_session(),
        )

    @staticmethod
    def layout_widgets(owner: QDialog) -> list[QWidget]:
        layout = owner._rule_approval_controls_layout
        return [
            layout.itemAt(index).widget()
            for index in range(layout.count())
            if layout.itemAt(index).widget() is not None
        ]

    def test_approval_rebuild_ten_deletes_replaced_controls_without_gc(self) -> None:
        owner = self.approval_owner()
        old_widgets: list[QWidget] = []

        for _index in range(10):
            old_widgets.extend(self.layout_widgets(owner))
            self.refresh_approval(owner)
            self.app.processEvents()

        self.flush_deferred_deletes()
        current_widgets = self.layout_widgets(owner)
        self.assertTrue(old_widgets)
        self.assertTrue(all(sip.isdeleted(widget) for widget in old_widgets))
        self.assertEqual(len(current_widgets), len(owner._rule_approval_controls_box.findChildren(QWidget)))
        self.assertEqual(2, len(owner._rule_approval_decision_widgets))
        self.assertIsNotNone(owner._rule_approval_save_button)
        owner.deleteLater()
        self.flush_deferred_deletes()

    def test_approval_burst_rebuild_keeps_live_controls_bounded(self) -> None:
        owner = self.approval_owner()
        self.refresh_approval(owner)
        expected_count = len(self.layout_widgets(owner))
        old_widgets: list[QWidget] = []

        for _index in range(49):
            old_widgets.extend(self.layout_widgets(owner))
            self.refresh_approval(owner)

        self.flush_deferred_deletes()
        self.assertTrue(all(sip.isdeleted(widget) for widget in old_widgets))
        self.assertEqual(expected_count, len(self.layout_widgets(owner)))
        self.assertEqual(expected_count, len(owner._rule_approval_controls_box.findChildren(QWidget)))
        owner.deleteLater()
        self.flush_deferred_deletes()

    def test_approval_rebuild_keeps_one_callback_per_current_control(self) -> None:
        decision_callback = Mock()
        save_callback = Mock()
        owner = self.approval_owner(decision_callback, save_callback)

        for _index in range(10):
            self.refresh_approval(owner)
        self.flush_deferred_deletes()

        combo = owner._rule_approval_decision_widgets["buy.groups[0].conditions"]
        combo.setCurrentText("APPROVED")
        owner._rule_approval_save_button.click()
        self.app.processEvents()
        decision_callback.assert_called_once_with(
            "buy.groups[0].conditions",
            "APPROVED",
        )
        save_callback.assert_called_once()
        owner.deleteLater()
        self.flush_deferred_deletes()

    def test_approval_dialog_delete_and_recreate_does_not_reuse_controls(self) -> None:
        first_owner = self.approval_owner()
        self.refresh_approval(first_owner)
        first_controls = list(first_owner._rule_approval_decision_widgets.values())
        first_owner.deleteLater()
        self.flush_deferred_deletes()
        self.assertTrue(all(sip.isdeleted(control) for control in first_controls))

        second_owner = self.approval_owner()
        self.refresh_approval(second_owner)
        second_controls = list(second_owner._rule_approval_decision_widgets.values())
        self.assertTrue(all(control not in first_controls for control in second_controls))
        self.assertTrue(all(not sip.isdeleted(control) for control in second_controls))
        second_owner.deleteLater()
        self.flush_deferred_deletes()

    def test_notification_restarts_timer_and_ignores_old_timeout(self) -> None:
        parent = QDialog()
        parent.show()
        popup = AutoTradeNotificationPopup(parent)
        popup.show_message("A", 80)
        QTest.qWait(30)
        popup.show_message("B", 180)
        self.app.processEvents()

        QTest.qWait(70)
        self.app.processEvents()
        self.assertTrue(popup.isVisible())
        self.assertEqual("B", popup.text())

        QTest.qWait(130)
        self.app.processEvents()
        self.assertFalse(popup.isVisible())
        parent.deleteLater()
        self.flush_deferred_deletes()

    def test_notification_repeated_and_burst_messages_use_one_timer(self) -> None:
        parent = QDialog()
        parent.show()
        popup = AutoTradeNotificationPopup(parent)

        for _index in range(10):
            popup.show_message("same", 500)
        for index in range(50):
            popup.show_message(f"message-{index}", 80)

        timers = popup.findChildren(QTimer)
        self.assertEqual(1, len(timers))
        self.assertIs(popup._hide_timer, timers[0])
        self.assertTrue(popup._hide_timer.isActive())
        self.assertEqual("message-49", popup.text())
        self.assertTrue(popup.isVisible())

        QTest.qWait(100)
        self.app.processEvents()
        self.assertFalse(popup.isVisible())
        self.assertFalse(popup._hide_timer.isActive())
        parent.deleteLater()
        self.flush_deferred_deletes()

    def test_notification_parent_close_stops_timer_without_stale_callback(self) -> None:
        parent = QDialog()
        parent.show()
        popup = AutoTradeNotificationPopup(parent)
        popup.show_message("closing", 500)
        self.assertTrue(popup._hide_timer.isActive())

        with patch.object(__import__("sys"), "excepthook") as excepthook:
            parent.close()
            self.app.processEvents()
            self.assertFalse(popup._hide_timer.isActive())
            QTest.qWait(50)
            self.app.processEvents()

        excepthook.assert_not_called()
        parent.deleteLater()
        self.flush_deferred_deletes()
        self.assertTrue(sip.isdeleted(popup))


if __name__ == "__main__":
    unittest.main()
