import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QMessageBox,
)

import gui_operation_environment as environment_dialog
from gui_toast import show_toast


class ToastMessageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

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
        self.app.processEvents()
        self.assertFalse(toast.isVisible())
        self.assertTrue(parent.isVisible())
        parent.close()

    def test_repeated_toast_replaces_previous_message(self) -> None:
        parent = QDialog()
        parent.show()
        first = show_toast(parent, "first", duration_ms=2000)
        second = show_toast(parent, "second", duration_ms=2000)
        self.app.processEvents()

        self.assertFalse(first.isVisible())
        self.assertTrue(second.isVisible())
        self.assertIs(second, parent._common_toast_message)
        self.assertEqual("second", second.message())
        parent.close()

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
        parent.close()

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
        parent.close()

    def test_environment_save_button_uses_toast_and_closes_dialog(self) -> None:
        host = QDialog()
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
        dialog.close()


if __name__ == "__main__":
    unittest.main()
