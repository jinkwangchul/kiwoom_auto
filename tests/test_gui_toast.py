import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import sip
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QMessageBox,
)

import gui_operation_environment as environment_dialog
from gui_toast import ToastMessage, show_toast
from tests.qt_test_support import (
    dispose_qt_widget,
    ensure_qapplication,
    flush_deferred_deletes,
)


class ToastMessageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = ensure_qapplication()

    def flush_deferred_deletes(self) -> None:
        flush_deferred_deletes(self.app)

    def delete_widget(self, widget: QDialog) -> None:
        widget.deleteLater()
        self.flush_deferred_deletes()

    def test_toast_is_non_modal_and_closes_after_duration(self) -> None:
        parent = QDialog()
        parent.resize(500, 300)
        parent.show()

        toast = show_toast(parent, "saved", duration_ms=30)
        self.app.processEvents()

        self.assertTrue(parent.isVisible())
        self.assertTrue(toast.isVisible())
        self.assertEqual("saved", toast.message())
        self.assertEqual(30, toast.duration_ms())
        self.assertFalse(toast.isModal())

        QTest.qWait(60)
        self.flush_deferred_deletes()
        self.assertTrue(sip.isdeleted(toast))
        self.assertEqual([], parent.findChildren(ToastMessage))
        self.assertEqual([], parent.findChildren(QTimer))
        self.assertTrue(parent.isVisible())
        self.delete_widget(parent)

    def test_repeated_toast_replaces_previous_message(self) -> None:
        parent = QDialog()
        parent.show()
        first = show_toast(parent, "first", duration_ms=2000)
        second = show_toast(parent, "second", duration_ms=2000)
        self.flush_deferred_deletes()

        self.assertTrue(sip.isdeleted(first))
        self.assertTrue(second.isVisible())
        self.assertIs(second, parent._common_toast_message)
        self.assertEqual("second", second.message())
        self.delete_widget(parent)

    def test_sequential_toasts_keep_one_live_child_and_timer(self) -> None:
        parent = QDialog()
        parent.show()
        toasts = []

        for _index in range(10):
            toasts.append(show_toast(parent, "same", duration_ms=5000))
            self.app.processEvents()

        active_timers = [
            timer
            for toast in parent.findChildren(ToastMessage)
            for timer in toast.findChildren(QTimer)
            if timer.isActive()
        ]
        self.assertEqual(1, len(active_timers))

        self.flush_deferred_deletes()
        live_toasts = parent.findChildren(ToastMessage)
        self.assertEqual(1, len(live_toasts))
        self.assertIs(toasts[-1], live_toasts[0])
        self.assertTrue(all(sip.isdeleted(toast) for toast in toasts[:-1]))
        self.delete_widget(parent)

    def test_burst_toasts_keep_lifecycle_bounded(self) -> None:
        parent = QDialog()
        parent.show()

        for index in range(50):
            show_toast(parent, f"burst-{index}", duration_ms=5000)

        active_timers = [
            timer
            for toast in parent.findChildren(ToastMessage)
            for timer in toast.findChildren(QTimer)
            if timer.isActive()
        ]
        self.assertEqual(1, len(active_timers))

        self.flush_deferred_deletes()
        self.assertEqual(1, len(parent.findChildren(ToastMessage)))
        self.delete_widget(parent)

    def test_replaced_toast_timeout_cannot_close_current_toast(self) -> None:
        parent = QDialog()
        parent.show()
        first = show_toast(parent, "first", duration_ms=40)
        QTest.qWait(10)
        second = show_toast(parent, "second", duration_ms=300)
        self.flush_deferred_deletes()

        self.assertTrue(sip.isdeleted(first))
        QTest.qWait(80)
        self.app.processEvents()
        self.assertTrue(second.isVisible())
        self.assertIs(second, parent._common_toast_message)

        QTest.qWait(260)
        self.flush_deferred_deletes()
        self.assertTrue(sip.isdeleted(second))
        self.assertIsNone(parent._common_toast_message)
        self.delete_widget(parent)

    def test_toasts_are_owned_independently_per_parent(self) -> None:
        first_parent = QDialog()
        second_parent = QDialog()
        first_parent.show()
        second_parent.show()
        replaced = show_toast(first_parent, "first-a", duration_ms=5000)
        second_toast = show_toast(second_parent, "second", duration_ms=5000)
        current = show_toast(first_parent, "first-b", duration_ms=5000)
        self.flush_deferred_deletes()

        self.assertTrue(sip.isdeleted(replaced))
        self.assertIs(current, first_parent._common_toast_message)
        self.assertIs(second_toast, second_parent._common_toast_message)
        self.assertTrue(current.isVisible())
        self.assertTrue(second_toast.isVisible())
        self.assertEqual(1, len(first_parent.findChildren(ToastMessage)))
        self.assertEqual(1, len(second_parent.findChildren(ToastMessage)))
        self.delete_widget(first_parent)
        self.delete_widget(second_parent)

    def test_toast_tracks_parent_dialog_center(self) -> None:
        parent = QDialog()
        parent.resize(500, 300)
        parent.move(80, 60)
        parent.show()
        toast = show_toast(parent, "centered", duration_ms=2000)
        self.app.processEvents()

        self.assertLessEqual(
            (toast.frameGeometry().center() - parent.frameGeometry().center()).manhattanLength(),
            1,
        )

        parent.move(260, 180)
        self.app.processEvents()
        self.assertLessEqual(
            (toast.frameGeometry().center() - parent.frameGeometry().center()).manhattanLength(),
            1,
        )
        self.delete_widget(parent)

    def test_toast_tracks_parent_bottom_right(self) -> None:
        parent = QDialog()
        parent.resize(500, 300)
        parent.move(80, 60)
        parent.show()
        toast = show_toast(
            parent,
            "bottom right",
            duration_ms=2000,
            position="bottom_right",
        )
        self.app.processEvents()

        margin = 18
        self.assertEqual(
            parent.frameGeometry().right() - margin,
            toast.frameGeometry().right(),
        )
        self.assertEqual(
            parent.frameGeometry().bottom() - margin,
            toast.frameGeometry().bottom(),
        )

        parent.move(260, 180)
        self.app.processEvents()
        self.assertEqual(
            parent.frameGeometry().right() - margin,
            toast.frameGeometry().right(),
        )
        self.assertEqual(
            parent.frameGeometry().bottom() - margin,
            toast.frameGeometry().bottom(),
        )
        self.delete_widget(parent)

    def test_environment_save_button_uses_toast_and_closes_dialog(self) -> None:
        host = QDialog()
        self.addCleanup(dispose_qt_widget, host, close=True)
        host.resize(900, 600)
        host.show()
        with patch.object(
            environment_dialog,
            "read_operation_policy",
            return_value={},
        ):
            dialog = environment_dialog.OperationEnvironmentSettingsDialog(host)
        dialog.show()
        self.app.processEvents()
        button_box = dialog.findChild(QDialogButtonBox)
        self.assertIsNotNone(button_box)
        save_button = button_box.button(QDialogButtonBox.Save)
        expected_policy = dialog.build_policy_from_widgets(
            dialog._validated_starting_budget_defaults()
        )

        with (
            patch.object(
                environment_dialog,
                "read_operation_policy",
                side_effect=[dict(expected_policy), dict(expected_policy)],
            ),
            patch.object(environment_dialog, "write_operation_policy") as writer,
            patch.object(environment_dialog, "append_changelog"),
            patch.object(environment_dialog, "show_toast") as toast,
            patch.object(QMessageBox, "information") as information,
            patch.object(QMessageBox, "critical") as critical,
            patch.object(QMessageBox, "exec_") as exec_dialog,
        ):
            QTest.mouseClick(save_button, Qt.LeftButton)

        writer.assert_called_once()
        toast.assert_called_once_with(
            parent=host,
            message="환경설정을 저장했습니다.",
            duration_ms=2000,
            position="center",
        )
        information.assert_not_called()
        critical.assert_not_called()
        exec_dialog.assert_not_called()
        self.assertFalse(dialog.isVisible())
        self.assertEqual(QDialog.Accepted, dialog.result())
        host.close()

    def test_environment_save_error_keeps_modal_error_dialog(self) -> None:
        with patch.object(
            environment_dialog,
            "read_operation_policy",
            return_value={},
        ):
            dialog = environment_dialog.OperationEnvironmentSettingsDialog()
        self.addCleanup(dispose_qt_widget, dialog, close=True)

        with (
            patch.object(
                environment_dialog,
                "write_operation_policy",
                side_effect=OSError("write failed"),
            ),
            patch.object(environment_dialog, "show_toast") as toast,
            patch.object(QMessageBox, "critical") as critical,
        ):
            dialog.accept()

        toast.assert_not_called()
        critical.assert_called_once()


if __name__ == "__main__":
    unittest.main()
