# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QApplication, QStyle

import gui_stock_instance_chart_window as chart_window
from gui_auto_trade_display import (
    AUTO_TRADE_SETTING_AMBER_TEXT_COLOR,
    AUTO_TRADE_SETTING_BADGE_BORDER_COLOR,
    AUTO_TRADE_SETTING_INACTIVE_TEXT_COLOR,
    auto_trade_operation_identity_color,
)


def _header_display(
    values: tuple[str, str, str],
    colors: tuple[str, str, str] = ("#2563eb", "#111111", "#5c4300"),
    identity_color: str = "#ea580c",
) -> chart_window.StockOperationHeaderDisplay:
    return chart_window.StockOperationHeaderDisplay(
        status=values[0],
        method=values[1],
        liquidation=values[2],
        status_color=colors[0],
        method_color=colors[1],
        liquidation_color=colors[2],
        identity_color=identity_color,
    )


def _projection(stock_code: str = "005930") -> dict[str, object]:
    return {
        "stock_code": stock_code,
        "stock_name": "삼성전자",
        "trade_date": "2026-08-12",
        "instance_id": "instance-a",
        "instance_name": "지표추종매매B",
        "bar_minutes": 5,
        "operation_title_display": "시간운영",
        "candles": [],
        "buy_signal_markers": [],
        "sell_signal_markers": [],
        "diagnostics": {},
        "pnl_available": False,
    }


class StockInstanceChartHeaderInfoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        for window in list(chart_window._OPEN_STOCK_INSTANCE_CHARTS.values()):
            window.close()
        self.app.processEvents()
        chart_window._OPEN_STOCK_INSTANCE_CHARTS.clear()

    def _window(
        self,
        values: tuple[str, str, str],
        *,
        badge_font: QFont | None = None,
        provider=None,
    ) -> chart_window.StockInstanceChartWindow:
        provider = provider or (lambda _code, _date: _projection())
        patches = [
            patch.object(
                chart_window,
                "project_stock_operation_header_display",
                return_value=_header_display(values),
            ),
            patch.object(
                chart_window.StockInstanceChartWindow,
                "_operation_stock_context",
                return_value=None,
            ),
        ]
        if badge_font is not None:
            patches.append(
                patch.object(
                    chart_window,
                    "_auto_trade_setting_badge_font",
                    return_value=badge_font,
                )
            )
        for active_patch in patches:
            active_patch.start()
        try:
            return chart_window.StockInstanceChartWindow(
                "005930",
                "2026-08-12",
                projection_provider=provider,
            )
        finally:
            for active_patch in reversed(patches):
                active_patch.stop()

    def test_header_projection_reuses_settings_status_method_liquidation_helpers(self) -> None:
        with TemporaryDirectory() as temp_dir:
            stock_dir = Path(temp_dir) / "005930_삼성전자"
            stock_dir.mkdir()
            config = {"operation_mode": "SCHEDULED"}
            state = {"status": "RUNNING", "holding_qty": 3}
            repository = Mock()
            repository.resolve_stock_dir.return_value = stock_dir
            with patch(
                "stock_repository.StockRepository",
                return_value=repository,
            ), patch(
                "runtime_io.read_json_dict",
                side_effect=lambda path: config if Path(path).name == "config.json" else state,
            ), patch(
                "gui_order_utils.pending_order_side_quantities",
                return_value=(1, 2),
            ), patch(
                "gui_auto_trade_policy.auto_trade_setting_trade_started",
                return_value=True,
            ), patch(
                "gui_auto_trade_policy.auto_trade_setting_current_session_trade_started",
                return_value=True,
            ), patch(
                "gui_auto_trade_policy.auto_trade_setting_display_status_for_current_session",
                return_value="매수/매도",
            ) as status_helper, patch(
                "gui_auto_trade_policy.auto_trade_setting_method_text",
                return_value="루틴",
            ) as method_helper, patch(
                "gui_auto_trade_policy.auto_trade_setting_liquidation_text",
                return_value="5분/시장가",
            ) as liquidation_helper, patch(
                "gui_auto_trade_policy.auto_trade_setting_liquidation_active",
                return_value=True,
            ), patch(
                "gui_auto_trade_policy.effective_liquidation_policy_for_config",
                return_value=({"method": "MARKET"}, False),
            ), patch(
                "gui_auto_trade_integrity.is_review_required_state",
                return_value=False,
            ), patch(
                "gui_auto_trade_integrity.is_operation_excluded",
                return_value=False,
            ):
                display = chart_window.project_stock_operation_header_display("005930")
                self.assertEqual(
                    ("매수/매도", "루틴", "5분/시장가"),
                    display.values,
                )
                self.assertEqual("#16a34a", display.status_color)
                self.assertEqual("#5c4300", display.liquidation_color)

        status_helper.assert_called_once()
        method_helper.assert_called_once_with("매수/매도", config, state)
        liquidation_helper.assert_called_once_with(
            config,
            "매수/매도",
            state,
            holding_qty=3,
        )

    def test_single_padded_outline_wraps_fixed_text_columns(self) -> None:
        samples = (
            ("감시/대기", "루틴", "5분/시장가"),
            ("조기마감", "현재가", "100분/현재가"),
            ("긴급정지", "이월", "-"),
        )
        window = self._window(samples[0])
        window.show()
        self.app.processEvents()
        self.assertEqual(3, window.operation_info_panel.layout().count())
        self.assertNotIn(
            "stockInstanceChartOperationInfoSeparator",
            window.styleSheet(),
        )
        self.assertIn("QFrame#stockInstanceChartOperationInfo", window.styleSheet())
        self.assertIn(
            f"border: 1px solid {AUTO_TRADE_SETTING_BADGE_BORDER_COLOR}",
            window.styleSheet(),
        )
        self.assertIn("border-radius: 3px", window.styleSheet())
        self.assertIn("background: transparent", window.styleSheet())
        margins = window.operation_info_panel.layout().contentsMargins()
        button_top, button_bottom = chart_window._button_content_vertical_margins(
            window.early_close_button
        )
        expected_top, expected_bottom = chart_window._operation_info_vertical_margins(
            window.early_close_button
        )
        self.assertEqual((8, expected_top, 8, expected_bottom), (
            margins.left(),
            margins.top(),
            margins.right(),
            margins.bottom(),
        ))
        self.assertEqual(
            window.early_close_button.style().pixelMetric(
                QStyle.PM_ButtonMargin,
                None,
                window.early_close_button,
            ),
            button_top + button_bottom,
        )
        self.assertEqual(max(1, button_top - 1), margins.top())
        self.assertEqual(max(1, button_bottom - 1), margins.bottom())
        self.assertEqual(
            window.operation_info_labels["status"].fontMetrics().height()
            + margins.top()
            + margins.bottom()
            + 2,
            window.operation_info_panel.height(),
        )
        widths = chart_window._stock_operation_header_segment_widths(
            window.operation_info_labels["status"].font()
        )
        baseline_panel_width = window.operation_info_panel.width()
        self.assertEqual(
            sum(widths.values())
            + (window.operation_info_panel.layout().spacing() * 2)
            + margins.left()
            + margins.right()
            + 2,
            baseline_panel_width,
        )
        baseline_columns = {
            key: (
                window.operation_info_labels[key].mapTo(
                    window.operation_info_panel,
                    window.operation_info_labels[key].rect().topLeft(),
                ).x(),
                window.operation_info_labels[key].width(),
            )
            for key in ("status", "method", "liquidation")
        }
        for values in samples:
            with self.subTest(values=values), patch.object(
                chart_window,
                "project_stock_operation_header_display",
                return_value=_header_display(values),
            ):
                window._update_operation_header_info()
                self.app.processEvents()
                self.assertEqual(
                    values,
                    tuple(
                        window.operation_info_labels[key].text()
                        for key in ("status", "method", "liquidation")
                    ),
                )
                self.assertEqual(baseline_panel_width, window.operation_info_panel.width())
                for key in ("status", "method", "liquidation"):
                    label = window.operation_info_labels[key]
                    self.assertTrue(label.alignment() & Qt.AlignLeft)
                    self.assertEqual(widths[key], label.width())
                    self.assertEqual(
                        baseline_columns[key],
                        (
                            label.mapTo(
                                window.operation_info_panel,
                                label.rect().topLeft(),
                            ).x(),
                            label.width(),
                        ),
                    )
        window.close()

    def test_right_actions_stack_at_edge_and_pnl_reserves_maximum_width(self) -> None:
        badge_font = QFont("Malgun Gothic", 8)
        window = self._window(
            ("매수/매도", "루틴", "100분/현재가"),
            badge_font=badge_font,
        )
        widths = chart_window._stock_operation_header_segment_widths(badge_font)
        self.assertEqual(
            widths["liquidation"],
            window.operation_info_labels["liquidation"].width(),
        )
        self.assertEqual(Qt.AlignCenter, window.info_labels["stock"].alignment())
        for button in (
            window.early_close_button,
            window.immediate_liquidation_button,
        ):
            self.assertEqual(badge_font.family(), button.font().family())
            self.assertEqual(badge_font.pointSize(), button.font().pointSize())
        window.show()
        self.app.processEvents()
        window.info_labels["stock"].setText(
            "005930 아주긴종목명이표시되는삼성전자"
        )
        self.app.processEvents()
        stock_label = window.info_labels["stock"]
        operation_panel = window.operation_info_panel
        left_x = window.header_left_block.mapTo(
            window,
            window.header_left_block.rect().topLeft(),
        ).x()
        right_x = window.header_right_block.mapTo(
            window,
            window.header_right_block.rect().topLeft(),
        ).x()
        self.assertLessEqual(
            left_x + window.header_left_block.width(),
            right_x,
        )
        stock_top = stock_label.mapTo(window, stock_label.rect().topLeft()).y()
        operation_top = operation_panel.mapTo(
            window,
            operation_panel.rect().topLeft(),
        ).y()
        self.assertLessEqual(stock_top + stock_label.height(), operation_top)
        left_center = window.header_left_block.mapTo(
            window,
            window.header_left_block.rect().center(),
        ).x()
        identity_center = stock_label.mapTo(
            window,
            stock_label.rect().center(),
        ).x()
        operation_center = operation_panel.mapTo(
            window,
            operation_panel.rect().center(),
        ).x()
        self.assertLessEqual(abs(left_center - identity_center), 1)
        self.assertLessEqual(abs(left_center - operation_center), 1)

        pnl_label = window.info_labels["cumulative_pnl"]
        expected_pnl_width = chart_window._stock_instance_chart_pnl_display_width(
            pnl_label.font()
        )
        self.assertEqual(expected_pnl_width, pnl_label.width())
        self.assertGreaterEqual(
            pnl_label.width(),
            pnl_label.fontMetrics().horizontalAdvance("-99,999,999(-99.99%)"),
        )
        early_rect = window.early_close_button.geometry()
        immediate_rect = window.immediate_liquidation_button.geometry()
        self.assertEqual(early_rect.x(), immediate_rect.x())
        self.assertEqual(early_rect.width(), immediate_rect.width())
        self.assertEqual(early_rect.height(), immediate_rect.height())
        self.assertLessEqual(early_rect.bottom(), immediate_rect.top())
        action_right = window.header_action_block.mapTo(
            window.header_right_block,
            window.header_action_block.rect().topRight(),
        ).x()
        self.assertEqual(window.header_right_block.width() - 1, action_right)
        pnl_right = pnl_label.mapTo(
            window,
            pnl_label.rect().topRight(),
        ).x()
        action_left = window.header_action_block.mapTo(
            window,
            window.header_action_block.rect().topLeft(),
        ).x()
        self.assertLess(pnl_right, action_left)
        left_vertical_center = window.header_left_block.mapTo(
            window,
            window.header_left_block.rect().center(),
        ).y()
        pnl_vertical_center = pnl_label.mapTo(
            window,
            pnl_label.rect().center(),
        ).y()
        self.assertLessEqual(abs(left_vertical_center - pnl_vertical_center), 1)
        window.close()

    def test_chart_applies_projected_settings_foreground_colors(self) -> None:
        colors = ("#ea580c", "#afb2b9", "#d97706")
        window = self._window(("조기마감", "시장가", "5분/시장가"))
        with patch.object(
            chart_window,
            "project_stock_operation_header_display",
            return_value=_header_display(
                ("조기마감", "시장가", "5분/시장가"),
                colors,
            ),
        ):
            window._update_operation_header_info()
        for key, color in zip(("status", "method", "liquidation"), colors):
            self.assertIn(color, window.operation_info_labels[key].styleSheet().lower())
        window.close()

    def test_identity_color_uses_emergency_then_common_current_running_priority(self) -> None:
        with TemporaryDirectory() as temp_dir:
            stock_dir = Path(temp_dir) / "005930_삼성전자"
            stock_dir.mkdir()
            config = {"operation_mode": "SCHEDULED"}
            state = {"status": "RUNNING", "trade_enabled": True, "holding_qty": 0}
            repository = Mock()
            repository.resolve_stock_dir.return_value = stock_dir

            class Owner:
                ready = False

                def startup_recovery_session_ready(self, refresh=False):
                    return self.ready

                def registered_operation_targets(self):
                    return [(stock_dir, "005930", "삼성전자")]

            owner = Owner()

            def read_json(path):
                return config if Path(path).name == "config.json" else state

            with patch(
                "stock_repository.StockRepository",
                return_value=repository,
            ), patch(
                "runtime_io.read_json_dict",
                side_effect=read_json,
            ), patch(
                "gui_auto_trade_run_control.read_json_dict",
                side_effect=read_json,
            ), patch(
                "gui_auto_trade_run_control.auto_trade_running_registered_operation_targets",
                side_effect=lambda _owner: (
                    [(stock_dir, "005930", "삼성전자")] if owner.ready else []
                ),
            ), patch(
                "gui_order_utils.pending_order_side_quantities",
                return_value=(0, 0),
            ), patch(
                "gui_auto_trade_policy.auto_trade_setting_display_status_for_current_session",
                return_value="매수/매도",
            ), patch(
                "gui_auto_trade_policy.auto_trade_setting_method_text",
                return_value="루틴",
            ), patch(
                "gui_auto_trade_policy.auto_trade_setting_liquidation_text",
                return_value="-",
            ), patch(
                "gui_auto_trade_policy.auto_trade_setting_liquidation_active",
                return_value=False,
            ), patch(
                "gui_auto_trade_policy.effective_liquidation_policy_for_config",
                return_value=({}, False),
            ), patch(
                "operation_policy_gate.read_operation_state",
                return_value={"emergency_stop": False},
            ):
                stale = chart_window.project_stock_operation_header_display(
                    "005930",
                    owner,
                )
                self.assertEqual(
                    auto_trade_operation_identity_color(
                        operation_excluded=False,
                        review_managed=False,
                        emergency_stopped=False,
                        current_running=False,
                    ),
                    stale.identity_color,
                )

                owner.ready = True
                running = chart_window.project_stock_operation_header_display(
                    "005930",
                    owner,
                )
                self.assertEqual(
                    auto_trade_operation_identity_color(
                        operation_excluded=False,
                        review_managed=False,
                        emergency_stopped=False,
                        current_running=True,
                    ),
                    running.identity_color,
                )

                state["status"] = "EMERGENCY_STOPPED"
                state["trade_enabled"] = False
                emergency = chart_window.project_stock_operation_header_display(
                    "005930",
                    owner,
                )
                self.assertEqual(
                    auto_trade_operation_identity_color(
                        operation_excluded=False,
                        review_managed=False,
                        emergency_stopped=True,
                        current_running=False,
                    ),
                    emergency.identity_color,
                )

    def test_identity_color_priority_covers_all_six_contract_cases(self) -> None:
        cases = (
            (True, False, False, False, AUTO_TRADE_SETTING_INACTIVE_TEXT_COLOR),
            (True, False, True, False, AUTO_TRADE_SETTING_INACTIVE_TEXT_COLOR),
            (False, True, False, False, AUTO_TRADE_SETTING_INACTIVE_TEXT_COLOR),
            (False, False, True, False, "#E60000"),
            (False, False, False, True, "#2563eb"),
            (False, False, False, False, AUTO_TRADE_SETTING_AMBER_TEXT_COLOR),
        )
        for excluded, review, emergency, running, expected in cases:
            with self.subTest(
                excluded=excluded,
                review=review,
                emergency=emergency,
                running=running,
            ):
                self.assertEqual(
                    expected.lower(),
                    auto_trade_operation_identity_color(
                        operation_excluded=excluded,
                        review_managed=review,
                        emergency_stopped=emergency,
                        current_running=running,
                    ).lower(),
                )

    def test_identity_color_refreshes_on_existing_widget(self) -> None:
        window = self._window(("감시/대기", "루틴", "-"))
        transitions = (
            AUTO_TRADE_SETTING_AMBER_TEXT_COLOR,
            "#2563eb",
            "#e60000",
            AUTO_TRADE_SETTING_AMBER_TEXT_COLOR,
        )
        for color in transitions:
            with patch.object(
                chart_window,
                "project_stock_operation_header_display",
                return_value=_header_display(
                    ("감시/대기", "루틴", "-"),
                    identity_color=color,
                ),
            ):
                window._update_operation_header_info()
            self.assertIn(
                color.lower(),
                window.info_labels["stock"].styleSheet().lower(),
            )
            self.assertEqual("005930 삼성전자", window.info_labels["stock"].text())
        window.close()

    def test_projection_error_and_pnl_refresh_preserve_header(self) -> None:
        def broken_provider(_code: str, _date: str):
            raise RuntimeError("broken")

        window = self._window(("검토종목", "루틴", "-"), provider=broken_provider)
        self.assertEqual("검토종목", window.operation_info_labels["status"].text())
        with patch.object(
            chart_window,
            "project_current_stock_pnl",
            return_value={"available": False},
        ):
            window.refresh_pnl_only()
        self.assertEqual(
            ("검토종목", "루틴", "-"),
            tuple(
                window.operation_info_labels[key].text()
                for key in ("status", "method", "liquidation")
            ),
        )
        window.close()

    def test_singleton_refresh_updates_header_without_new_window(self) -> None:
        current = {
            "values": ("감시/대기", "루틴", "-"),
            "identity_color": AUTO_TRADE_SETTING_AMBER_TEXT_COLOR,
        }

        def values(_code: str, _owner=None):
            return _header_display(
                current["values"],
                identity_color=current["identity_color"],
            )

        with patch.object(
            chart_window,
            "project_stock_operation_header_display",
            side_effect=values,
        ), patch.object(
            chart_window,
            "project_stock_instance_day",
            return_value=_projection(),
        ), patch.object(
            chart_window.StockInstanceChartWindow,
            "_operation_stock_context",
            return_value=None,
        ):
            first = chart_window.open_stock_instance_chart("005930")
            self.app.processEvents()
            baseline_geometry = (
                first.operation_info_panel.width(),
                tuple(
                    (
                        first.operation_info_labels[key].width(),
                        first.operation_info_labels[key].mapTo(
                            first.operation_info_panel,
                            first.operation_info_labels[key].rect().topLeft(),
                        ).x(),
                    )
                    for key in ("status", "method", "liquidation")
                ),
            )
            current["values"] = ("조기마감", "시장가", "100분/시장가")
            current["identity_color"] = "#2563eb"
            second = chart_window.open_stock_instance_chart("005930")
            self.app.processEvents()

        self.assertIs(first, second)
        self.assertIn(
            current["identity_color"],
            second.info_labels["stock"].styleSheet().lower(),
        )
        self.assertEqual(
            current["values"],
            tuple(
                second.operation_info_labels[key].text()
                for key in ("status", "method", "liquidation")
            ),
        )
        self.assertEqual(
            baseline_geometry,
            (
                second.operation_info_panel.width(),
                tuple(
                    (
                        second.operation_info_labels[key].width(),
                        second.operation_info_labels[key].mapTo(
                            second.operation_info_panel,
                            second.operation_info_labels[key].rect().topLeft(),
                        ).x(),
                    )
                    for key in ("status", "method", "liquidation")
                ),
            ),
        )
        first.close()


if __name__ == "__main__":
    unittest.main()
