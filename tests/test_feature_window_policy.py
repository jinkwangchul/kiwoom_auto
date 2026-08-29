# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PyQt5 import sip
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QDialog, QMainWindow, QMessageBox

from gui_window_policy import (
    close_persistent_feature_windows,
    configure_persistent_feature_window,
    persistent_feature_owner,
)


class FeatureWindowPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_persistent_windows_are_top_level_modeless_with_logical_owner(self) -> None:
        owner = QMainWindow()
        settings = QDialog()
        chart = QDialog()
        self.addCleanup(owner.close)
        self.addCleanup(settings.close)
        self.addCleanup(chart.close)

        configure_persistent_feature_window(settings, owner)
        configure_persistent_feature_window(chart, settings)

        self.assertIsNone(settings.parentWidget())
        self.assertIsNone(chart.parentWidget())
        self.assertTrue(settings.isWindow())
        self.assertTrue(chart.isWindow())
        self.assertEqual(Qt.NonModal, settings.windowModality())
        self.assertEqual(Qt.NonModal, chart.windowModality())
        self.assertIs(owner, persistent_feature_owner(settings))
        self.assertIs(owner, persistent_feature_owner(chart))

    def test_reopening_visible_review_window_refreshes_before_raise(self) -> None:
        import gui_windows

        owner = QMainWindow()
        existing = QDialog()
        existing.refresh_review_items = Mock()
        owner.review_required_window = existing
        self.addCleanup(owner.close)
        self.addCleanup(existing.close)
        existing.show()
        self.app.processEvents()

        gui_windows.MainWindow.open_review_required_window(owner)

        existing.refresh_review_items.assert_called_once_with()
        self.assertTrue(existing.isVisible())

    def test_transient_dialog_keeps_the_actual_feature_window_parent(self) -> None:
        owner = QMainWindow()
        feature = QDialog()
        self.addCleanup(owner.close)
        self.addCleanup(feature.close)
        configure_persistent_feature_window(feature, owner)

        message = QMessageBox(feature)
        message.setWindowModality(Qt.WindowModal)
        self.addCleanup(message.close)

        self.assertIs(feature, message.parentWidget())
        self.assertEqual(Qt.WindowModal, message.windowModality())

    def test_logical_owner_closes_all_registered_feature_windows(self) -> None:
        owner = QMainWindow()
        first = QDialog()
        second = QDialog()
        self.addCleanup(owner.close)
        configure_persistent_feature_window(first, owner)
        configure_persistent_feature_window(second, owner)
        first.show()
        second.show()
        self.app.processEvents()

        close_persistent_feature_windows(owner)
        self.app.processEvents()

        self.assertFalse(first.isVisible())
        self.assertFalse(second.isVisible())
        self.assertEqual(0, len(owner._persistent_feature_windows))

    def test_close_all_continues_past_deleted_window_and_keeps_owner_alive(self) -> None:
        owner = QMainWindow()
        stale = QDialog()
        live = QDialog()
        self.addCleanup(owner.close)
        self.addCleanup(live.close)
        configure_persistent_feature_window(stale, owner)
        configure_persistent_feature_window(live, owner)
        owner.show()
        live.show()
        self.app.processEvents()
        sip.delete(stale)

        close_persistent_feature_windows(owner)
        self.app.processEvents()

        self.assertTrue(owner.isVisible())
        self.assertFalse(live.isVisible())
        self.assertEqual(0, len(owner._persistent_feature_windows))

    def test_close_all_respects_a_feature_window_close_rejection(self) -> None:
        class RejectingDialog(QDialog):
            def closeEvent(self, event) -> None:  # type: ignore[override]
                event.ignore()

        owner = QMainWindow()
        feature = RejectingDialog()
        self.addCleanup(owner.close)
        self.addCleanup(sip.delete, feature)
        configure_persistent_feature_window(feature, owner)
        owner.show()
        feature.show()
        self.app.processEvents()

        close_persistent_feature_windows(owner)
        self.app.processEvents()

        self.assertTrue(owner.isVisible())
        self.assertTrue(feature.isVisible())
        self.assertIn(feature, owner._persistent_feature_windows)

    def test_representative_feature_windows_use_the_common_policy(self) -> None:
        from gui_event_record_window import EventRecordPrototypeWindow
        from gui_log_view_window import LogViewWindow
        from gui_operation_environment import OperationEnvironmentSettingsDialog
        from gui_order_status_window import OrderStatusWindow
        from gui_review_required_window import GlobalReviewRequiredWindow
        from gui_stock_performance_window import StockPerformanceWindow
        from gui_stock_register_window import StockRegisterWindow

        owner = QMainWindow()
        self.addCleanup(owner.close)
        with (
            TemporaryDirectory() as temp_dir,
            patch.object(
                GlobalReviewRequiredWindow,
                "refresh_review_items",
                lambda _self: None,
            ),
            patch.object(StockRegisterWindow, "refresh_stock_table", lambda _self: None),
            patch.object(EventRecordPrototypeWindow, "select_period", lambda _self, _period: None),
        ):
            stock_dir = Path(temp_dir)
            windows = [
                LogViewWindow(stock_dir, "routine", "005930", "Samsung", owner),
                OrderStatusWindow(stock_dir, "routine", "005930", "Samsung", owner),
                GlobalReviewRequiredWindow(owner),
                StockRegisterWindow(owner),
                StockPerformanceWindow(owner),
                OperationEnvironmentSettingsDialog(owner),
                EventRecordPrototypeWindow(owner),
            ]
            for window in windows:
                self.addCleanup(window.close)
                self.assertIsNone(window.parentWidget())
                self.assertTrue(window.isWindow())
                self.assertEqual(Qt.NonModal, window.windowModality())
                self.assertIs(owner, persistent_feature_owner(window))
                window.show()

            self.app.processEvents()
            close_persistent_feature_windows(owner)
            self.app.processEvents()
            self.assertTrue(all(not window.isVisible() for window in windows))
            self.assertEqual(0, len(owner._persistent_feature_windows))


if __name__ == "__main__":
    unittest.main()
