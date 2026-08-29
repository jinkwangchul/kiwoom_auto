# -*- coding: utf-8 -*-

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from PyQt5.QtCore import QMargins, QPoint, QRect, QSize
from PyQt5.QtGui import QFont, QFontMetrics
from PyQt5.QtWidgets import QApplication

import gui_main
import gui_main_table_loader
import gui_windows


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

    def screen(self):
        return None


class _FakeMarginsLayout:
    def __init__(self, left: int, right: int, spacing: int = 0) -> None:
        self._margins = QMargins(left, 0, right, 0)
        self._spacing = spacing

    def contentsMargins(self) -> QMargins:
        return self._margins

    def spacing(self) -> int:
        return self._spacing


class _FakeScrollBar:
    def __init__(self, width: int) -> None:
        self._width = width

    def sizeHint(self) -> QSize:
        return QSize(self._width, 0)


class _FakeStyle:
    def __init__(self, scrollbar_extent: int) -> None:
        self._scrollbar_extent = scrollbar_extent

    def pixelMetric(self, _metric, _option, _widget) -> int:
        return self._scrollbar_extent


class _FakeRoutineTable:
    def __init__(
        self,
        font: QFont,
        frame_width: int,
        scrollbar_width: int,
        scrollbar_extent: int,
    ) -> None:
        self._font = font
        self._frame_width = frame_width
        self._scrollbar = _FakeScrollBar(scrollbar_width)
        self._style = _FakeStyle(scrollbar_extent)

    def font(self) -> QFont:
        return self._font

    def frameWidth(self) -> int:
        return self._frame_width

    def verticalScrollBar(self) -> _FakeScrollBar:
        return self._scrollbar

    def style(self) -> _FakeStyle:
        return self._style


class _FakeBadgeArea:
    def __init__(self, width: int) -> None:
        self._width = width

    def width(self) -> int:
        return self._width


class MainWindowStartupPositionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_center_uses_cursor_screen_available_geometry_without_resizing(self) -> None:
        available = QRect(1920, 80, 1600, 900)
        cursor_screen = _FakeScreen(available)
        app = Mock()
        app.screenAt.return_value = cursor_screen
        window = _FakeWindow(QRect(25, 40, 1000, 600))
        original_size = window.frame_size()

        with patch.object(gui_main.QCursor, "pos", return_value=QPoint(2200, 200)):
            centered = gui_main.center_main_window_on_active_screen(app, window)

        expected_frame = QRect(25, 40, 1000, 600)
        expected_frame.moveCenter(available.center())
        self.assertTrue(centered)
        self.assertEqual([expected_frame.topLeft()], window.move_calls)
        self.assertEqual(original_size, window.frame_size())
        app.screenAt.assert_called_once_with(QPoint(2200, 200))
        app.primaryScreen.assert_not_called()

    def test_primary_screen_is_final_fallback(self) -> None:
        available = QRect(120, 80, 1600, 900)
        app = Mock()
        app.screenAt.return_value = None
        app.primaryScreen.return_value = _FakeScreen(available)
        window = _FakeWindow(QRect(25, 40, 1000, 600))

        centered = gui_main.center_main_window_on_active_screen(app, window)

        expected_frame = QRect(25, 40, 1000, 600)
        expected_frame.moveCenter(available.center())
        self.assertTrue(centered)
        self.assertEqual([expected_frame.topLeft()], window.move_calls)

    def test_missing_screen_leaves_window_untouched(self) -> None:
        app = Mock()
        app.screenAt.return_value = None
        app.primaryScreen.return_value = None
        window = _FakeWindow(QRect(25, 40, 1000, 600))

        centered = gui_main.center_main_window_on_active_screen(app, window)

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
            "center_main_window_on_active_screen",
            side_effect=center_once,
        ) as center:
            result = gui_main.main()

        self.assertEqual(0, result)
        self.assertEqual(["show", "center", "exec"], calls)
        center.assert_called_once_with(app, window)

    def test_required_width_ends_at_painted_consumed_metric(self) -> None:
        font = gui_main_table_loader.main_monitoring_table_font()
        frame_width = 1
        scrollbar_width = 14
        scrollbar_extent = 16
        badge_width = 68
        content_spacing = 6
        window = Mock()
        window.routine_table = _FakeRoutineTable(
            font,
            frame_width,
            scrollbar_width,
            scrollbar_extent,
        )
        window._main_routine_filter_badge_area = _FakeBadgeArea(badge_width)
        window._main_routine_content_layout = _FakeMarginsLayout(
            0,
            0,
            content_spacing,
        )
        window._main_routine_layout = _FakeMarginsLayout(8, 8)
        window._main_table_area_layout = _FakeMarginsLayout(0, 0)
        window._main_dashboard_layout = _FakeMarginsLayout(8, 8)
        window._layout_horizontal_margins = (
            gui_main.MainWindow._layout_horizontal_margins
        )
        window.minimumWidth.return_value = 1680

        actual = gui_main.MainWindow._main_control_window_required_width(window)

        column_widths = gui_main_table_loader.routine_stock_column_widths(font)
        metrics = QFontMetrics(font)
        base_column_count = 7
        base_metric_left = (
            gui_main_table_loader.ROUTINE_STOCK_TEXT_OFFSET
            + sum(column_widths[:base_column_count])
            + gui_main_table_loader.routine_instance_separator_width(font)
            * (base_column_count - 1)
        )
        metric_slot_widths = gui_windows._main_stock_metric_slot_widths(metrics)
        metric_gap = gui_windows.ROUTINE_STOCK_METRIC_SEPARATOR_GAP
        row_content_width = (
            base_metric_left
            + metric_gap
            + sum(metric_slot_widths)
            + max(0, len(metric_slot_widths) - 1)
            * (metric_gap * 2 + max(1, metrics.horizontalAdvance("|")))
        )
        expected = (
            row_content_width
            + metrics.horizontalAdvance("0")
            + metrics.horizontalAdvance("한")
            + (frame_width * 2)
            + max(scrollbar_width, scrollbar_extent)
            + badge_width
            + content_spacing
            + 16
            + 16
        )
        self.assertEqual(expected, actual)
        self.assertGreater(actual, 2137)

    def test_width_application_preserves_current_height(self) -> None:
        window = Mock()
        window._main_control_window_required_width.return_value = 2943
        window.height.return_value = 720

        gui_main.MainWindow._apply_main_control_window_width(window)

        window.resize.assert_called_once_with(2943, 720)


if __name__ == "__main__":
    unittest.main()
