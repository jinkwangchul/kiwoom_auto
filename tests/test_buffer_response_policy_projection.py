# -*- coding: utf-8 -*-

from __future__ import annotations

import inspect
import os
import sys
import unittest
from decimal import Decimal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QWidget

import buffer_response_policy_projection as projection


class _TextControl:
    def __init__(self, text: str) -> None:
        self.value = text

    def currentText(self) -> str:
        return self.value

    def text(self) -> str:
        return self.value


class _Surface:
    def __init__(self, *, mode: str = "UNIFIED", threshold: str = "80%") -> None:
        self.visible = True
        self.mode = mode
        self.buffer_close_ratio_combo = _TextControl(threshold)
        self.strategy_rows = {
            "unified": [(_TextControl("손익금액"), _TextControl("낮은순"))],
            "profit": [(_TextControl("손익비율"), _TextControl("높은순"))],
            "loss": [(_TextControl("투입금액"), _TextControl("낮은순"))],
        }
        self.strategy_action_badges = {
            "unified": _TextControl("구간마감"),
            "profit": _TextControl("조기마감"),
            "loss": _TextControl("즉시청산"),
        }

    def isVisible(self) -> bool:
        return self.visible

    def application_mode(self) -> str:
        return self.mode


def _pnl(
    profit: object,
    *,
    rate: object = "5.5",
    open_cost: object = 1000,
    open_buy_reservation: object = 999999,
) -> dict[str, object]:
    return {
        "available": True,
        "cumulative_profit": profit,
        "cumulative_rate": rate,
        "open_cost": open_cost,
        # Deliberately present to prove it cannot contaminate the factor value.
        "open_buy_reservation": open_buy_reservation,
    }


def _activity(ratio: object) -> dict[str, object]:
    return {"available": True, "entry_amount": 1, "entry_ratio": ratio}


class BufferResponsePolicyProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_reads_the_open_real_settings_surface_widget_state(self) -> None:
        import gui_windows

        owner = QWidget()
        surface = gui_windows._BufferResponseSettingsSurface(owner)
        try:
            surface.show()
            self.app.processEvents()
            surface.segmented_checkbox.click()
            surface.strategy_rows["profit"][0][0].setCurrentText("투입금액")
            surface.strategy_rows["profit"][0][1].setCurrentText("낮은순")
            surface.buffer_close_ratio_combo.setCurrentText("60%")
            settings = projection.read_buffer_response_settings(surface)
            self.assertTrue(settings["available"])
            self.assertEqual("SEGMENTED", settings["application_mode"])
            self.assertEqual(60, settings["configured_threshold"])
            self.assertEqual(
                {
                    "evaluation_factor": "투입금액",
                    "direction": "낮은순",
                    "response_mode": surface.strategy_action_badges["profit"].text(),
                },
                settings["strategies"]["profit"],
            )
        finally:
            surface.close()
            owner.deleteLater()

    def test_dynamic_threshold_boundaries(self) -> None:
        for threshold in (10, 40, 60, 80, 90):
            with self.subTest(threshold=threshold, side="below"):
                surface = _Surface(threshold=f"{threshold}%")
                result = projection.project_buffer_response_policy(
                    settings_surface=surface,
                    pnl_by_stock={"005930": _pnl(100)},
                    budget_activity=_activity(Decimal(threshold) - Decimal("0.001")),
                )
                self.assertTrue(result["applicable"])
                self.assertEqual(threshold, result["configured_threshold"])
                self.assertEqual("EARLY_CLOSE", result["effective_response"])
            with self.subTest(threshold=threshold, side="equal"):
                result = projection.project_buffer_response_policy(
                    settings_surface=_Surface(threshold=f"{threshold}%"),
                    pnl_by_stock={"005930": _pnl(100)},
                    budget_activity=_activity(Decimal(threshold)),
                )
                self.assertEqual(
                    "IMMEDIATE_LIQUIDATION_REQUIRED",
                    result["effective_response"],
                )

    def test_all_ui_threshold_options_are_read_dynamically(self) -> None:
        for threshold in range(10, 100, 10):
            with self.subTest(threshold=threshold):
                settings = projection.read_buffer_response_settings(
                    _Surface(threshold=f"{threshold}%")
                )
                self.assertTrue(settings["available"])
                self.assertEqual(threshold, settings["configured_threshold"])

    def test_unified_mode_uses_live_row_settings(self) -> None:
        surface = _Surface()
        surface.strategy_rows["unified"][0][0].value = "손익비율"
        surface.strategy_rows["unified"][0][1].value = "높은순"
        surface.strategy_action_badges["unified"].value = "조기마감"
        result = projection.project_buffer_response_policy(
            settings_surface=surface,
            pnl_by_stock={"005930": _pnl(100, rate="7.25")},
            budget_activity=_activity("50"),
        )
        self.assertEqual("UNIFIED", result["application_mode"])
        self.assertEqual("", result["selected_segment"])
        self.assertEqual("cumulative_rate", result["evaluation_field"])
        self.assertEqual("DESCENDING", result["sort_direction"])
        self.assertEqual(Decimal("7.25"), result["candidate_factor_values"]["005930"])
        self.assertEqual("EARLY_CLOSE", result["effective_response"])

    def test_segmented_profit_and_loss_select_independent_rows(self) -> None:
        profit_surface = _Surface(mode="SEGMENTED")
        profit_result = projection.project_buffer_response_policy(
            settings_surface=profit_surface,
            pnl_by_stock={"005930": _pnl(10), "000660": _pnl(-10)},
            budget_activity=_activity("20"),
        )
        self.assertEqual("PROFIT", profit_result["selected_segment"])
        self.assertEqual("손익비율", profit_result["evaluation_factor"])
        self.assertEqual("EARLY_CLOSE", profit_result["effective_response"])

        loss_surface = _Surface(mode="SEGMENTED")
        loss_result = projection.project_buffer_response_policy(
            settings_surface=loss_surface,
            pnl_by_stock={"005930": _pnl(-1), "000660": _pnl(-2)},
            budget_activity=_activity("20"),
        )
        self.assertEqual("LOSS", loss_result["selected_segment"])
        self.assertEqual("투입금액", loss_result["evaluation_factor"])
        self.assertEqual("ASCENDING", loss_result["sort_direction"])
        self.assertEqual(
            "IMMEDIATE_LIQUIDATION_REQUIRED",
            loss_result["effective_response"],
        )

    def test_factor_mapping_uses_profit_rate_and_open_cost_only(self) -> None:
        cases = (
            ("손익금액", "cumulative_profit", Decimal("123")),
            ("손익비율", "cumulative_rate", Decimal("4.5")),
            ("투입금액", "open_cost", Decimal("700")),
        )
        for factor, field, expected in cases:
            with self.subTest(factor=factor):
                surface = _Surface()
                surface.strategy_rows["unified"][0][0].value = factor
                result = projection.project_buffer_response_policy(
                    settings_surface=surface,
                    pnl_by_stock={
                        "005930": _pnl(
                            123,
                            rate="4.5",
                            open_cost=700,
                            open_buy_reservation=9000,
                        )
                    },
                    budget_activity=_activity("30"),
                )
                self.assertEqual(field, result["evaluation_field"])
                self.assertEqual(expected, result["candidate_factor_values"]["005930"])
        self.assertNotEqual(
            Decimal("9700"),
            result["candidate_factor_values"]["005930"],
        )

    def test_missing_or_invalid_inputs_fail_closed(self) -> None:
        hidden = _Surface()
        hidden.visible = False
        invalid_threshold = _Surface(threshold="75%")
        cases = (
            (hidden, {"005930": _pnl(1)}, _activity(20)),
            (invalid_threshold, {"005930": _pnl(1)}, _activity(20)),
            (_Surface(), {}, _activity(20)),
            (_Surface(), {"005930": _pnl(1)}, {"available": False}),
            (_Surface(), {"005930": _pnl(1)}, {"available": True, "entry_amount": 0, "entry_ratio": 0}),
        )
        for surface, pnl, activity in cases:
            with self.subTest(reason=(surface, pnl, activity)):
                result = projection.project_buffer_response_policy(
                    settings_surface=surface,
                    pnl_by_stock=pnl,
                    budget_activity=activity,
                )
                self.assertFalse(result["available"])
                self.assertFalse(result["applicable"])
                self.assertTrue(result["reason"])

    def test_projection_module_has_no_execution_or_writer_dependency(self) -> None:
        source = inspect.getsource(projection)
        for forbidden in (
            "close_intent_service",
            "operation_command_service",
            "auto_trade_order_execution_boundary",
            "runtime_io",
            "SendOrder",
            "chejan_event_recorder",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
