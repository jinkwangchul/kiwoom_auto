# -*- coding: utf-8 -*-

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from PyQt5.QtCore import QPoint, QRect, QSize

import gui_main


class _FakeScreen:
    def __init__(self, available_geometry: QRect) -> None:
        self._available_geometry = QRect(available_geometry)

    def availableGeometry(self) -> QRect:
        return QRect(self._available_geometry)


class _FakeWindow:
    def __init__(self, frame_geometry: QRect) -> None:
        self._frame_geometry = QRect(frame_geometry)
        self.move_calls: list[QPoint] = []

    def frameGeometry(self) -> QRect:
        return QRect(self._frame_geometry)

    def move(self, position: QPoint) -> None:
        point = QPoint(position)
        self.move_calls.append(point)
        self._frame_geometry.moveTopLeft(point)

    def frame_size(self) -> QSize:
        return self._frame_geometry.size()


class MainWindowStartupPositionTest(unittest.TestCase):
    def test_center_uses_primary_available_geometry_without_resizing(self) -> None:
        available = QRect(120, 80, 1600, 900)
        app = Mock()
        app.primaryScreen.return_value = _FakeScreen(available)
        window = _FakeWindow(QRect(25, 40, 1000, 600))
        original_size = window.frame_size()

        centered = gui_main.center_main_window_on_primary_screen(app, window)

        expected_frame = QRect(25, 40, 1000, 600)
        expected_frame.moveCenter(available.center())
        self.assertTrue(centered)
        self.assertEqual([expected_frame.topLeft()], window.move_calls)
        self.assertEqual(original_size, window.frame_size())
        app.primaryScreen.assert_called_once_with()

    def test_missing_primary_screen_leaves_window_untouched(self) -> None:
        app = Mock()
        app.primaryScreen.return_value = None
        window = _FakeWindow(QRect(25, 40, 1000, 600))

        centered = gui_main.center_main_window_on_primary_screen(app, window)

        self.assertFalse(centered)
        self.assertEqual([], window.move_calls)

    def test_main_centers_once_after_show(self) -> None:
        calls: list[str] = []
        app = Mock()
        app.exec_.side_effect = lambda: calls.append("exec") or 0
        window = Mock()
        window.show.side_effect = lambda: calls.append("show")

        def center_once(actual_app, actual_window) -> bool:
            self.assertIs(app, actual_app)
            self.assertIs(window, actual_window)
            calls.append("center")
            return True

        with patch.object(gui_main, "install_global_exception_observers"), patch.object(
            gui_main, "QApplication", return_value=app
        ), patch.object(gui_main, "MainWindow", return_value=window), patch.object(
            gui_main,
            "center_main_window_on_primary_screen",
            side_effect=center_once,
        ) as center:
            result = gui_main.main()

        self.assertEqual(0, result)
        self.assertEqual(["show", "center", "exec"], calls)
        center.assert_called_once_with(app, window)


if __name__ == "__main__":
    unittest.main()
