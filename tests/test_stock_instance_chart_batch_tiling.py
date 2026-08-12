# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from PyQt5.QtCore import QRect, QSize

import gui_auto_trade_context_menu as context_menu
import gui_stock_instance_chart_window as chart_window


class _FakeScreen:
    def __init__(self, geometry: QRect) -> None:
        self._geometry = QRect(geometry)

    def availableGeometry(self) -> QRect:
        return QRect(self._geometry)


class _FakeParent:
    def __init__(self, screen: _FakeScreen) -> None:
        self._screen = screen

    def screen(self) -> _FakeScreen:
        return self._screen


class _FakeChartWindow:
    def __init__(
        self,
        *,
        minimum_width: int = 820,
        minimum_height: int = 428,
        width: int = 1040,
        height: int = 525,
        frame_extra_width: int = 0,
        frame_extra_height: int = 0,
        x: int = 0,
        y: int = 0,
    ) -> None:
        self._minimum_width = minimum_width
        self._minimum_height = minimum_height
        self._width = width
        self._height = height
        self._frame_extra_width = frame_extra_width
        self._frame_extra_height = frame_extra_height
        self._x = x
        self._y = y

    def minimumSize(self) -> QSize:
        return QSize(self._minimum_width, self._minimum_height)

    def minimumWidth(self) -> int:
        return self._minimum_width

    def minimumHeight(self) -> int:
        return self._minimum_height

    def width(self) -> int:
        return self._width

    def height(self) -> int:
        return self._height

    def resize(self, width: int, height: int) -> None:
        self._width = max(self._minimum_width, int(width))
        self._height = max(self._minimum_height, int(height))

    def move(self, x: int, y: int) -> None:
        self._x = int(x)
        self._y = int(y)

    def x(self) -> int:
        return self._x

    def y(self) -> int:
        return self._y

    def frameGeometry(self) -> QRect:
        return QRect(
            self._x,
            self._y,
            self._width + self._frame_extra_width,
            self._height + self._frame_extra_height,
        )


class StockInstanceChartBatchTilingTests(unittest.TestCase):
    def test_one_new_chart_uses_first_minimum_size_tile_slot(self) -> None:
        chart = _FakeChartWindow(width=1040, height=525, x=300, y=200)
        screen = _FakeScreen(QRect(0, 0, 1920, 1080))

        context_menu.tile_new_stock_instance_charts(
            None,
            [chart],
            screens=[screen],
            primary_screen=screen,
        )

        self.assertEqual((820, 428), (chart.width(), chart.height()))
        self.assertEqual((0, 0), (chart.x(), chart.y()))

    def test_two_new_charts_tile_left_to_right_without_overlap(self) -> None:
        charts = [_FakeChartWindow(), _FakeChartWindow()]
        screen = _FakeScreen(QRect(100, 50, 1648, 900))

        context_menu.tile_new_stock_instance_charts(
            None,
            charts,
            screens=[screen],
            primary_screen=screen,
        )

        self.assertEqual([(100, 50), (928, 50)], [(w.x(), w.y()) for w in charts])
        self.assertFalse(charts[0].frameGeometry().intersects(charts[1].frameGeometry()))
        self.assertTrue(all((w.width(), w.height()) == (820, 428) for w in charts))

    def test_four_new_charts_use_two_by_two_grid(self) -> None:
        charts = [_FakeChartWindow() for _index in range(4)]
        screen = _FakeScreen(QRect(20, 30, 1648, 864))

        context_menu.tile_new_stock_instance_charts(
            None,
            charts,
            screens=[screen],
            primary_screen=screen,
        )

        self.assertEqual(
            [(20, 30), (848, 30), (20, 466), (848, 466)],
            [(window.x(), window.y()) for window in charts],
        )
        for index, first in enumerate(charts):
            for second in charts[index + 1 :]:
                self.assertFalse(first.frameGeometry().intersects(second.frameGeometry()))

    def test_existing_chart_is_not_moved_and_only_new_charts_are_tiled(self) -> None:
        existing = _FakeChartWindow(x=700, y=400)
        new_charts = [_FakeChartWindow() for _index in range(3)]
        by_code = {
            "005930": existing,
            "012330": new_charts[0],
            "086520": new_charts[1],
            "247540": new_charts[2],
        }
        selected = [
            (Path(code), code, code)
            for code in ("005930", "012330", "086520", "247540")
        ]

        with (
            patch(
                "gui_stock_instance_chart_window.open_stock_instance_chart",
                side_effect=lambda code, **_kwargs: by_code[code],
            ),
            patch.object(context_menu, "tile_new_stock_instance_charts") as tile,
        ):
            opened = context_menu.open_selected_stock_instance_charts(None, selected)

        self.assertEqual(list(by_code.values()), opened)
        tile.assert_not_called()
        self.assertEqual((700, 400), (existing.x(), existing.y()))

    def test_duplicate_selection_does_not_consume_a_tile_slot(self) -> None:
        first = _FakeChartWindow()
        second = _FakeChartWindow()
        selected = [
            (Path("first"), "005930", "삼성전자"),
            (Path("duplicate"), "005930", "삼성전자 중복"),
            (Path("second"), "012330", "현대모비스"),
        ]

        with (
            patch(
                "gui_stock_instance_chart_window.open_stock_instance_chart",
                side_effect=[first, second],
            ) as opener,
            patch.object(context_menu, "tile_new_stock_instance_charts") as tile,
        ):
            opened = context_menu.open_selected_stock_instance_charts(None, selected)

        self.assertEqual([first, second], opened)
        self.assertEqual(2, opener.call_count)
        tile.assert_not_called()

    def test_single_open_fills_first_free_slot_without_moving_existing_charts(self) -> None:
        screen = _FakeScreen(QRect(0, 0, 2484, 1300))
        existing = [
            _FakeChartWindow(width=820, height=428, x=x, y=y)
            for x, y in ((0, 0), (828, 0), (1656, 0), (0, 436))
        ]
        original_positions = [(window.x(), window.y()) for window in existing]
        new_chart = _FakeChartWindow()

        with patch.object(
            chart_window,
            "_live_stock_instance_charts",
            return_value=existing,
        ):
            context_menu.tile_new_stock_instance_charts(
                None,
                [new_chart],
                screens=[screen],
                primary_screen=screen,
            )

        self.assertEqual((828, 436), (new_chart.x(), new_chart.y()))
        self.assertEqual(original_positions, [(window.x(), window.y()) for window in existing])

    def test_closed_middle_slot_is_reused_by_the_next_single_open(self) -> None:
        screen = _FakeScreen(QRect(0, 0, 2484, 1300))
        existing = [
            _FakeChartWindow(width=820, height=428, x=0, y=0),
            _FakeChartWindow(width=820, height=428, x=1656, y=0),
        ]
        new_chart = _FakeChartWindow()

        with patch.object(
            chart_window,
            "_live_stock_instance_charts",
            return_value=existing,
        ):
            context_menu.tile_new_stock_instance_charts(
                None,
                [new_chart],
                screens=[screen],
                primary_screen=screen,
            )

        self.assertEqual((828, 0), (new_chart.x(), new_chart.y()))

    def test_manually_moved_chart_stays_put_and_releases_its_old_slot(self) -> None:
        screen = _FakeScreen(QRect(0, 0, 2484, 1300))
        moved = _FakeChartWindow(width=820, height=428, x=1656, y=872)
        new_chart = _FakeChartWindow()

        with patch.object(
            chart_window,
            "_live_stock_instance_charts",
            return_value=[moved],
        ):
            context_menu.tile_new_stock_instance_charts(
                None,
                [new_chart],
                screens=[screen],
                primary_screen=screen,
            )

        self.assertEqual((1656, 872), (moved.x(), moved.y()))
        self.assertEqual((0, 0), (new_chart.x(), new_chart.y()))

    def test_existing_four_plus_batch_three_fill_only_next_slots(self) -> None:
        screen = _FakeScreen(QRect(0, 0, 2484, 1300))
        existing = [
            _FakeChartWindow(width=820, height=428, x=x, y=y)
            for x, y in ((0, 0), (828, 0), (1656, 0), (0, 436))
        ]
        original_positions = [(window.x(), window.y()) for window in existing]
        new_charts = [_FakeChartWindow() for _index in range(3)]

        with patch.object(
            chart_window,
            "_live_stock_instance_charts",
            return_value=existing,
        ):
            context_menu.tile_new_stock_instance_charts(
                None,
                new_charts,
                screens=[screen],
                primary_screen=screen,
            )

        self.assertEqual(
            [(828, 436), (1656, 436), (0, 872)],
            [(window.x(), window.y()) for window in new_charts],
        )
        self.assertEqual(original_positions, [(window.x(), window.y()) for window in existing])

    def test_full_primary_continues_at_first_free_secondary_slot(self) -> None:
        primary = _FakeScreen(QRect(0, 0, 1648, 864))
        secondary = _FakeScreen(QRect(2000, 100, 1648, 864))
        existing = [
            _FakeChartWindow(width=820, height=428, x=x, y=y)
            for x, y in ((0, 0), (828, 0), (0, 436), (828, 436))
        ]
        new_chart = _FakeChartWindow()

        with patch.object(
            chart_window,
            "_live_stock_instance_charts",
            return_value=existing,
        ):
            context_menu.tile_new_stock_instance_charts(
                None,
                [new_chart],
                screens=[secondary, primary],
                primary_screen=primary,
            )

        self.assertEqual((2000, 100), (new_chart.x(), new_chart.y()))

    def test_calling_feature_window_screen_has_first_slot_priority(self) -> None:
        first = _FakeScreen(QRect(0, 0, 1648, 864))
        caller_screen = _FakeScreen(QRect(2000, 100, 1648, 864))
        new_chart = _FakeChartWindow()

        with patch.object(
            chart_window,
            "_live_stock_instance_charts",
            return_value=[],
        ):
            context_menu.tile_new_stock_instance_charts(
                _FakeParent(caller_screen),
                [new_chart],
                screens=[first, caller_screen],
            )

        self.assertEqual((2000, 100), (new_chart.x(), new_chart.y()))

    def test_capacity_continues_on_the_next_screen(self) -> None:
        charts = [_FakeChartWindow() for _index in range(3)]
        primary = _FakeScreen(QRect(0, 0, 1648, 428))
        secondary = _FakeScreen(QRect(2000, 100, 1648, 428))

        context_menu.tile_new_stock_instance_charts(
            None,
            charts,
            screens=[primary, secondary],
            primary_screen=primary,
        )

        self.assertEqual([(0, 0), (828, 0), (2000, 100)], [(w.x(), w.y()) for w in charts])

    def test_six_charts_fill_first_screen_then_continue_on_second(self) -> None:
        charts = [_FakeChartWindow() for _index in range(6)]
        primary = _FakeScreen(QRect(0, 0, 1648, 864))
        secondary = _FakeScreen(QRect(2000, 100, 1648, 864))

        context_menu.tile_new_stock_instance_charts(
            None,
            charts,
            screens=[primary, secondary],
            primary_screen=primary,
        )

        self.assertEqual(
            [
                (0, 0),
                (828, 0),
                (0, 436),
                (828, 436),
                (2000, 100),
                (2828, 100),
            ],
            [(window.x(), window.y()) for window in charts],
        )

    def test_capacity_overflow_preserves_minimum_and_stays_anchored_on_screen(self) -> None:
        charts = [_FakeChartWindow() for _index in range(3)]
        primary = _FakeScreen(QRect(50, 40, 820, 428))
        secondary = _FakeScreen(QRect(1000, 100, 820, 428))

        context_menu.tile_new_stock_instance_charts(
            None,
            charts,
            screens=[primary, secondary],
            primary_screen=primary,
        )

        self.assertEqual((50, 40), (charts[0].x(), charts[0].y()))
        self.assertEqual((1000, 100), (charts[1].x(), charts[1].y()))
        self.assertEqual((50, 40), (charts[2].x(), charts[2].y()))
        for chart in charts:
            self.assertGreaterEqual(chart.width(), chart.minimumWidth())
            self.assertGreaterEqual(chart.height(), chart.minimumHeight())

    def test_frame_overhead_is_included_in_grid_capacity(self) -> None:
        charts = [
            _FakeChartWindow(frame_extra_width=16, frame_extra_height=39)
            for _index in range(2)
        ]
        primary = _FakeScreen(QRect(10, 20, 1680, 467))

        context_menu.tile_new_stock_instance_charts(
            None,
            charts,
            screens=[primary],
            primary_screen=primary,
        )

        self.assertEqual([(10, 20), (854, 20)], [(w.x(), w.y()) for w in charts])
        self.assertLessEqual(charts[1].frameGeometry().right(), primary.availableGeometry().right())


if __name__ == "__main__":
    unittest.main()
