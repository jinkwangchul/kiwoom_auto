from __future__ import annotations

import inspect
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton, QWidget

import gui_main_budget_panel as panel
import gui_operation_environment as environment


class _TextTarget:
    def __init__(self) -> None:
        self.text = ""
        self.style_sheet = ""

    def setText(self, text: str) -> None:
        self.text = text

    def setStyleSheet(self, style_sheet: str) -> None:
        self.style_sheet = style_sheet


class _MemorySettings:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}
        self.set_calls: list[tuple[str, object]] = []

    def value(self, key: str, default=None):
        return self.values.get(key, default)

    def setValue(self, key: str, value: object) -> None:
        self.values[key] = value
        self.set_calls.append((key, value))


class MainBudgetPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_global_total_budget_default_is_user_confirmed_two_million(self) -> None:
        policy = environment.default_operation_policy()
        self.assertEqual(
            {
                "total_budget": 2_000_000,
                "available_budget_percent": 100,
            },
            environment.system_budget_policy(policy),
        )

    def test_system_budget_writer_loader_and_restart_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy_path = Path(temp_dir) / "operation_policy.json"
            saved = environment.write_system_budget_policy(
                total_budget=2_000_000,
                available_budget_percent=90,
                path=policy_path,
            )
            reloaded = environment.read_system_budget_policy(path=policy_path)

        self.assertEqual(saved, reloaded)
        self.assertEqual(2_000_000, reloaded["total_budget"])
        self.assertEqual(90, reloaded["available_budget_percent"])

    def test_system_budget_writer_preserves_other_global_policy_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy_path = Path(temp_dir) / "operation_policy.json"
            original = environment.default_operation_policy()
            original["regular_market"] = {
                "start_time": "08:30:00",
                "end_time": "15:20:00",
            }
            environment.write_operation_policy(original, path=policy_path)
            environment.write_system_budget_policy(
                total_budget=3_000_000,
                available_budget_percent=80,
                path=policy_path,
            )
            reloaded = environment.read_operation_policy(path=policy_path)

        self.assertEqual("08:30:00", reloaded["regular_market"]["start_time"])
        self.assertEqual(3_000_000, reloaded["system_budget"]["total_budget"])

    def test_total_budget_validation_supports_confirmed_max_and_fails_closed(self) -> None:
        self.assertEqual(
            9_999_999_999,
            environment.validate_system_total_budget("9,999,999,999"),
        )
        for invalid in (-1, 10_000_000_000, 1.5, True, "abc"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    environment.validate_system_total_budget(invalid)

        malformed = {
            "system_budget": {
                "total_budget": "invalid",
                "available_budget_percent": 90,
            }
        }
        self.assertEqual(0, environment.system_budget_policy(malformed)["total_budget"])
        self.assertEqual(
            0,
            environment.system_budget_policy({"system_budget": "invalid"})[
                "total_budget"
            ],
        )

    def test_projection_uses_only_global_policy_not_routine_budget(self) -> None:
        source = inspect.getsource(panel.collect_main_budget_summary)
        self.assertNotIn("read_routine_budget", source)
        self.assertNotIn("get_routine_dirs", source)
        with (
            patch.object(
                panel,
                "read_system_budget_policy",
                return_value={
                    "total_budget": 2_000_000,
                    "available_budget_percent": 100,
                },
            ),
            patch(
                "gui_routine_registry.read_routine_budget",
                side_effect=AssertionError("routine budget must not be read"),
            ),
        ):
            summary = panel.collect_main_budget_summary()

        self.assertEqual(2_000_000, summary["total_budget"])
        self.assertEqual(2_000_000, summary["available_budget"])

    def test_available_ninety_projects_buffer_ten(self) -> None:
        summary = panel.project_system_budget_amounts(2_000_000, 90)
        self.assertEqual(90, summary["available_budget_percent"])
        self.assertEqual(10, summary["buffer_budget_percent"])
        self.assertEqual(1_800_000, summary["available_budget"])
        self.assertEqual(200_000, summary["buffer_budget"])
        self.assertTrue(summary["buffer_enabled"])

    def test_total_budget_change_recalculates_both_derived_amounts(self) -> None:
        summary = panel.project_system_budget_amounts(3_000_000, 80)

        self.assertEqual(2_400_000, summary["available_budget"])
        self.assertEqual(600_000, summary["buffer_budget"])
        self.assertEqual(
            summary["total_budget"],
            summary["available_budget"] + summary["buffer_budget"],
        )

    def test_all_orderable_cash_percentage_options_calculate_fixed_amounts(self) -> None:
        expected = {
            100: 500_000_000,
            90: 450_000_000,
            80: 400_000_000,
            70: 350_000_000,
            60: 300_000_000,
            50: 250_000_000,
            40: 200_000_000,
            30: 150_000_000,
            20: 100_000_000,
            10: 50_000_000,
        }
        self.assertEqual(
            tuple(expected),
            panel.MAIN_TOTAL_BUDGET_PERCENT_OPTIONS,
        )
        for percent, amount in expected.items():
            with self.subTest(percent=percent):
                self.assertEqual(
                    amount,
                    panel.total_budget_from_orderable_cash(500_000_000, percent),
                )

    def test_digit_alignment_matches_confirmed_100_to_10_percent_simulation(self) -> None:
        expected = {
            100: 453_780_000,
            90: 410_000_000,
            80: 360_000_000,
            70: 320_000_000,
            60: 270_000_000,
            50: 230_000_000,
            40: 180_000_000,
            30: 140_000_000,
            20: 91_000_000,
            10: 45_000_000,
        }
        for percent, amount in expected.items():
            with self.subTest(percent=percent):
                self.assertEqual(
                    amount,
                    panel.total_budget_from_orderable_cash(
                        453_780_000,
                        percent,
                        align_digits=True,
                    ),
                )

    def test_digit_alignment_off_keeps_raw_percentage_amount(self) -> None:
        self.assertEqual(
            408_402_000,
            panel.total_budget_from_orderable_cash(
                453_780_000,
                90,
                align_digits=False,
            ),
        )
        self.assertEqual(
            45_378_000,
            panel.total_budget_from_orderable_cash(
                453_780_000,
                10,
                align_digits=False,
            ),
        )

    def test_one_hundred_percent_is_never_digit_aligned(self) -> None:
        self.assertEqual(
            453_780_000,
            panel.total_budget_from_orderable_cash(
                453_780_000,
                100,
                align_digits=True,
            ),
        )

    def test_direct_amount_persistence_never_applies_digit_alignment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy_path = Path(temp_dir) / "operation_policy.json"
            environment.write_system_budget_policy(
                total_budget=2_000_000,
                available_budget_percent=90,
                path=policy_path,
            )
            panel.persist_main_total_budget(
                "4,537,800",
                policy_path=policy_path,
            )
            stored = environment.read_system_budget_policy(path=policy_path)

        self.assertEqual(4_537_800, stored["total_budget"])

    def test_total_budget_writer_preserves_available_percent_and_restart_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy_path = Path(temp_dir) / "operation_policy.json"
            environment.write_system_budget_policy(
                total_budget=2_000_000,
                available_budget_percent=90,
                path=policy_path,
            )
            projected = panel.persist_main_total_budget(
                "3,000,000",
                policy_path=policy_path,
            )
            reloaded = environment.read_system_budget_policy(path=policy_path)

        self.assertEqual(3_000_000, reloaded["total_budget"])
        self.assertEqual(90, reloaded["available_budget_percent"])
        self.assertEqual(2_700_000, projected["available_budget"])
        self.assertEqual(300_000, projected["buffer_budget"])

    def test_total_budget_percentage_is_not_persisted_as_a_policy(self) -> None:
        with (
            patch.object(
                panel,
                "read_system_budget_policy",
                return_value={
                    "total_budget": 2_000_000,
                    "available_budget_percent": 80,
                },
            ),
            patch.object(
                panel,
                "write_system_budget_policy",
                return_value={
                    "total_budget": 9_000_000,
                    "available_budget_percent": 80,
                },
            ) as writer,
        ):
            fixed_amount = panel.total_budget_from_orderable_cash(10_000_000, 90)
            panel.persist_main_total_budget(fixed_amount)

        writer.assert_called_once_with(
            total_budget=9_000_000,
            available_budget_percent=80,
            path=None,
        )

    def test_direct_total_budget_validates_maximum_and_current_orderable_cash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy_path = Path(temp_dir) / "operation_policy.json"
            environment.write_system_budget_policy(
                total_budget=2_000_000,
                available_budget_percent=100,
                path=policy_path,
            )
            with self.assertRaises(ValueError):
                panel.persist_main_total_budget(
                    10_000_000_000,
                    policy_path=policy_path,
                )
            with self.assertRaises(ValueError):
                panel.persist_main_total_budget(
                    3_000_000,
                    orderable_cash=2_000_000,
                    policy_path=policy_path,
                )
            unchanged = environment.read_system_budget_policy(path=policy_path)

        self.assertEqual(2_000_000, unchanged["total_budget"])

    def test_large_orderable_cash_does_not_reduce_total_budget_ui_maximum(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy_path = Path(temp_dir) / "operation_policy.json"
            environment.write_system_budget_policy(
                total_budget=2_000_000,
                available_budget_percent=100,
                path=policy_path,
            )
            projected = panel.persist_main_total_budget(
                9_999_999_999,
                orderable_cash=20_000_000_000,
                policy_path=policy_path,
            )

        self.assertEqual(9_999_999_999, projected["total_budget"])

    def test_buffer_input_twenty_persists_available_eighty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy_path = Path(temp_dir) / "operation_policy.json"
            environment.write_system_budget_policy(
                total_budget=2_000_000,
                available_budget_percent=100,
                path=policy_path,
            )
            summary = panel.persist_main_budget_percent(
                "buffer",
                "20",
                policy_path=policy_path,
            )
            stored = environment.read_system_budget_policy(path=policy_path)

        self.assertEqual(80, stored["available_budget_percent"])
        self.assertEqual(20, summary["buffer_budget_percent"])

    def test_buffer_zero_is_the_only_edit_path_to_available_one_hundred(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy_path = Path(temp_dir) / "operation_policy.json"
            environment.write_system_budget_policy(
                total_budget=2_000_000,
                available_budget_percent=90,
                path=policy_path,
            )
            with self.assertRaises(ValueError):
                panel.persist_main_budget_percent(
                    "available",
                    "100",
                    policy_path=policy_path,
                )
            summary = panel.persist_main_budget_percent(
                "buffer",
                "0",
                policy_path=policy_path,
            )

        self.assertEqual(100, summary["available_budget_percent"])
        self.assertEqual(0, summary["buffer_budget_percent"])
        self.assertEqual(2_000_000, summary["available_budget"])
        self.assertFalse(summary["buffer_enabled"])

    def test_disallowed_zero_and_one_hundred_inputs_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy_path = Path(temp_dir) / "operation_policy.json"
            environment.write_system_budget_policy(
                total_budget=2_000_000,
                available_budget_percent=90,
                path=policy_path,
            )
            for source, value in (
                ("available", "0"),
                ("available", "100"),
                ("buffer", "100"),
            ):
                with self.subTest(source=source, value=value):
                    with self.assertRaises(ValueError):
                        panel.persist_main_budget_percent(
                            source,
                            value,
                            policy_path=policy_path,
                        )

    def test_update_projects_percent_and_disabled_buffer_display(self) -> None:
        window = SimpleNamespace(
            _main_budget_orderable_valid=True,
            budget_total_label=_TextTarget(),
            budget_available_label=_TextTarget(),
            budget_reserve_label=_TextTarget(),
            budget_available_percent_edit=_TextTarget(),
            budget_buffer_percent_edit=_TextTarget(),
            budget_available_percent_suffix_label=_TextTarget(),
            budget_buffer_percent_suffix_label=_TextTarget(),
        )
        summary = panel.project_system_budget_amounts(2_000_000, 100)
        with patch.object(panel, "collect_main_budget_summary", return_value=summary):
            panel.update_main_budget_panel(window)

        self.assertEqual("2,000,000", window.budget_total_label.text)
        self.assertEqual("2,000,000", window.budget_available_label.text)
        self.assertEqual("-", window.budget_reserve_label.text)
        self.assertEqual("-", window.budget_available_percent_edit.text)
        self.assertEqual("-", window.budget_buffer_percent_edit.text)
        self.assertEqual(" :", window.budget_available_percent_suffix_label.text)
        self.assertEqual(" :", window.budget_buffer_percent_suffix_label.text)

    def test_metric_dash_is_centered_without_changing_numeric_right_alignment(self) -> None:
        numeric = QLabel()
        dash = QLabel()
        numeric.setFixedWidth(180)
        dash.setFixedWidth(180)

        panel.set_metric_value_text(numeric, "2,000,000")
        panel.set_metric_value_text(dash, "-")

        self.assertEqual(Qt.AlignRight | Qt.AlignVCenter, numeric.alignment())
        self.assertEqual(Qt.AlignCenter, dash.alignment())
        self.assertEqual(numeric.width(), dash.width())

    def test_update_projects_active_percent_without_parentheses(self) -> None:
        window = SimpleNamespace(
            _main_budget_orderable_valid=True,
            budget_total_label=_TextTarget(),
            budget_available_label=_TextTarget(),
            budget_reserve_label=_TextTarget(),
            budget_available_percent_edit=_TextTarget(),
            budget_buffer_percent_edit=_TextTarget(),
            budget_available_percent_suffix_label=_TextTarget(),
            budget_buffer_percent_suffix_label=_TextTarget(),
        )
        summary = panel.project_system_budget_amounts(2_000_000, 90)
        with patch.object(panel, "collect_main_budget_summary", return_value=summary):
            panel.update_main_budget_panel(window)

        self.assertEqual("90", window.budget_available_percent_edit.text)
        self.assertEqual("10", window.budget_buffer_percent_edit.text)
        self.assertEqual("% :", window.budget_available_percent_suffix_label.text)
        self.assertEqual("% :", window.budget_buffer_percent_suffix_label.text)
        self.assertNotIn("(", window.budget_available_percent_suffix_label.text)
        self.assertNotIn(")", window.budget_buffer_percent_suffix_label.text)

    def test_unconfirmed_account_keeps_ratios_but_hides_derived_amounts(self) -> None:
        for validation_state in (None, False):
            with self.subTest(validation_state=validation_state):
                window = SimpleNamespace(
                    _main_budget_orderable_valid=validation_state,
                    budget_total_label=_TextTarget(),
                    budget_available_label=_TextTarget(),
                    budget_reserve_label=_TextTarget(),
                    budget_available_percent_edit=_TextTarget(),
                    budget_buffer_percent_edit=_TextTarget(),
                    budget_available_percent_suffix_label=_TextTarget(),
                    budget_buffer_percent_suffix_label=_TextTarget(),
                    budget_available_remaining_label=_TextTarget(),
                    budget_buffer_entry_label=_TextTarget(),
                    budget_available_remaining_ratio_label=_TextTarget(),
                    budget_buffer_entry_ratio_label=_TextTarget(),
                )
                summary = panel.project_system_budget_amounts(400_000_000, 70)
                with patch.object(
                    panel,
                    "collect_main_budget_summary",
                    return_value=summary,
                ):
                    panel.update_main_budget_panel(window)

                self.assertEqual("400,000,000", window.budget_total_label.text)
                self.assertEqual("70", window.budget_available_percent_edit.text)
                self.assertEqual("30", window.budget_buffer_percent_edit.text)
                self.assertEqual("-", window.budget_available_label.text)
                self.assertEqual("-", window.budget_reserve_label.text)
                self.assertEqual("-", window.budget_available_remaining_label.text)
                self.assertEqual("-", window.budget_buffer_entry_label.text)
                self.assertEqual("-", window.budget_available_remaining_ratio_label.text)
                self.assertEqual("-", window.budget_buffer_entry_ratio_label.text)

    def test_confirmed_account_projects_available_and_buffer_amounts(self) -> None:
        window = SimpleNamespace(
            _main_budget_orderable_valid=True,
            budget_total_label=_TextTarget(),
            budget_available_label=_TextTarget(),
            budget_reserve_label=_TextTarget(),
            budget_available_percent_edit=_TextTarget(),
            budget_buffer_percent_edit=_TextTarget(),
            budget_available_percent_suffix_label=_TextTarget(),
            budget_buffer_percent_suffix_label=_TextTarget(),
            budget_available_remaining_label=_TextTarget(),
            budget_buffer_entry_label=_TextTarget(),
            budget_available_remaining_ratio_label=_TextTarget(),
            budget_buffer_entry_ratio_label=_TextTarget(),
        )
        summary = panel.project_system_budget_amounts(400_000_000, 70)
        with patch.object(panel, "collect_main_budget_summary", return_value=summary):
            panel.update_main_budget_panel(window)

        self.assertEqual("280,000,000", window.budget_available_label.text)
        self.assertEqual("120,000,000", window.budget_reserve_label.text)
        self.assertEqual("-", window.budget_available_remaining_label.text)
        self.assertEqual("-", window.budget_buffer_entry_label.text)
        self.assertEqual("-", window.budget_available_remaining_ratio_label.text)
        self.assertEqual("-", window.budget_buffer_entry_ratio_label.text)

    def test_activity_simulations_a_b_c_use_verified_consumed_amount(self) -> None:
        summary = panel.project_system_budget_amounts(400_000_000, 70)
        expected = {
            0: (280_000_000, "100.0", 0, "0.0", False),
            100_000_000: (180_000_000, "64.3", 0, "0.0", False),
            300_000_000: (0, "0.0", 20_000_000, "16.7", False),
            410_000_000: (0, "0.0", 130_000_000, "108.3", True),
        }
        for consumed, values in expected.items():
            with self.subTest(consumed=consumed):
                activity = panel.project_main_budget_activity(
                    summary,
                    {"available": True, "consumed_amount": consumed},
                )
                remaining, remaining_ratio, entry, entry_ratio, exceeded = values
                self.assertTrue(activity["available"])
                self.assertEqual(remaining, activity["remaining_amount"])
                self.assertEqual(remaining_ratio, str(activity["remaining_ratio"]))
                self.assertEqual(entry, activity["entry_amount"])
                self.assertEqual(entry_ratio, str(activity["entry_ratio"]))
                self.assertEqual(exceeded, activity["policy_exceeded"])

    def test_verified_consumption_updates_a_b_c_labels_without_ratio_clamp(self) -> None:
        summary = panel.project_system_budget_amounts(400_000_000, 70)
        expected = {
            0: ("280,000,000", "100.0%", "0", "0.0%"),
            100_000_000: ("180,000,000", "64.3%", "0", "0.0%"),
            300_000_000: ("0", "0.0%", "20,000,000", "16.7%"),
            410_000_000: ("0", "0.0%", "130,000,000", "108.3%"),
        }
        for consumed_amount, projected in expected.items():
            with self.subTest(consumed_amount=consumed_amount):
                response_badge_states: list[bool] = []
                window = SimpleNamespace(
                    _main_budget_orderable_valid=True,
                    budget_total_label=_TextTarget(),
                    budget_available_label=_TextTarget(),
                    budget_reserve_label=_TextTarget(),
                    budget_available_percent_edit=_TextTarget(),
                    budget_buffer_percent_edit=_TextTarget(),
                    budget_available_percent_suffix_label=_TextTarget(),
                    budget_buffer_percent_suffix_label=_TextTarget(),
                    budget_available_remaining_label=_TextTarget(),
                    budget_buffer_entry_label=_TextTarget(),
                    budget_available_remaining_ratio_label=_TextTarget(),
                    budget_buffer_entry_ratio_label=_TextTarget(),
                    _apply_main_budget_buffer_response_badge_style=(
                        response_badge_states.append
                    ),
                )
                with (
                    patch.object(
                        panel,
                        "collect_main_budget_summary",
                        return_value=summary,
                    ),
                    patch.object(
                        panel,
                        "collect_main_account_budget_consumption",
                        return_value={
                            "available": True,
                            "consumed_amount": consumed_amount,
                        },
                    ),
                ):
                    panel.update_main_budget_panel(window)

                self.assertEqual(
                    projected,
                    (
                        window.budget_available_remaining_label.text,
                        window.budget_available_remaining_ratio_label.text,
                        window.budget_buffer_entry_label.text,
                        window.budget_buffer_entry_ratio_label.text,
                    ),
                )
                expected_entry_style = (
                    f"color: {panel.DIRECTIONAL_NEGATIVE_COLOR};"
                    if consumed_amount > 280_000_000
                    else ""
                )
                self.assertEqual(
                    expected_entry_style,
                    window.budget_buffer_entry_label.style_sheet,
                )
                self.assertEqual(
                    expected_entry_style,
                    window.budget_buffer_entry_ratio_label.style_sheet,
                )
                self.assertEqual(
                    [consumed_amount > 280_000_000],
                    response_badge_states,
                )

    def test_disabled_buffer_keeps_entry_projection_unavailable(self) -> None:
        summary = panel.project_system_budget_amounts(400_000_000, 100)
        activity = panel.project_main_budget_activity(
            summary,
            {"available": True, "consumed_amount": 100_000_000},
        )
        self.assertTrue(activity["available"])
        self.assertEqual(300_000_000, activity["remaining_amount"])
        self.assertEqual("75.0", str(activity["remaining_ratio"]))
        self.assertIsNone(activity["entry_amount"])
        self.assertIsNone(activity["entry_ratio"])

    def test_budget_warning_available_threshold_crossings_are_directional(self) -> None:
        ninety = panel.project_main_budget_warning_transition(
            previous_available_remaining_ratio="95.0",
            previous_buffer_entered=False,
            activity={"available": True, "remaining_ratio": "89.0", "entry_amount": 0},
            buffer_enabled=True,
        )
        self.assertEqual(90, ninety["available_threshold_crossed"])

        baseline = panel.project_main_budget_warning_transition(
            previous_available_remaining_ratio=None,
            previous_buffer_entered=None,
            activity={"available": True, "remaining_ratio": "85.0", "entry_amount": 0},
            buffer_enabled=True,
        )
        self.assertIsNone(baseline["available_threshold_crossed"])

        decreased = panel.project_main_budget_warning_transition(
            previous_available_remaining_ratio=baseline["available_remaining_ratio"],
            previous_buffer_entered=baseline["buffer_entered"],
            activity={"available": True, "remaining_ratio": "79.0", "entry_amount": 0},
            buffer_enabled=True,
        )
        self.assertEqual(80, decreased["available_threshold_crossed"])

        same_band = panel.project_main_budget_warning_transition(
            previous_available_remaining_ratio=decreased["available_remaining_ratio"],
            previous_buffer_entered=decreased["buffer_entered"],
            activity={"available": True, "remaining_ratio": "75.0", "entry_amount": 0},
            buffer_enabled=True,
        )
        self.assertIsNone(same_band["available_threshold_crossed"])

        recovered = panel.project_main_budget_warning_transition(
            previous_available_remaining_ratio="79.0",
            previous_buffer_entered=False,
            activity={"available": True, "remaining_ratio": "81.0", "entry_amount": 0},
            buffer_enabled=True,
        )
        self.assertEqual(80, recovered["available_threshold_crossed"])

    def test_budget_warning_multi_threshold_jump_reports_one_final_boundary(self) -> None:
        transition = panel.project_main_budget_warning_transition(
            previous_available_remaining_ratio="95.0",
            previous_buffer_entered=False,
            activity={"available": True, "remaining_ratio": "64.0", "entry_amount": 0},
            buffer_enabled=True,
        )

        self.assertEqual(70, transition["available_threshold_crossed"])

    def test_budget_warning_initial_and_unavailable_projection_do_not_replay(self) -> None:
        initial = panel.project_main_budget_warning_transition(
            previous_available_remaining_ratio=None,
            previous_buffer_entered=None,
            activity={"available": True, "remaining_ratio": "64.3", "entry_amount": 20},
            buffer_enabled=True,
        )
        self.assertIsNone(initial["available_threshold_crossed"])
        self.assertFalse(initial["buffer_entry_started"])

        unavailable = panel.project_main_budget_warning_transition(
            previous_available_remaining_ratio="85.0",
            previous_buffer_entered=False,
            activity={"available": False},
            buffer_enabled=True,
        )
        self.assertFalse(unavailable["available"])
        self.assertIsNone(unavailable["available_remaining_ratio"])
        self.assertIsNone(unavailable["buffer_entered"])

    def test_budget_warning_buffer_entry_rearms_only_after_full_recovery(self) -> None:
        entered = panel.project_main_budget_warning_transition(
            previous_available_remaining_ratio="10.0",
            previous_buffer_entered=False,
            activity={"available": True, "remaining_ratio": "0.0", "entry_amount": 20_000_000},
            buffer_enabled=True,
        )
        self.assertTrue(entered["buffer_entry_started"])

        changed_inside = panel.project_main_budget_warning_transition(
            previous_available_remaining_ratio=entered["available_remaining_ratio"],
            previous_buffer_entered=entered["buffer_entered"],
            activity={"available": True, "remaining_ratio": "0.0", "entry_amount": 30_000_000},
            buffer_enabled=True,
        )
        self.assertFalse(changed_inside["buffer_entry_started"])

        recovered = panel.project_main_budget_warning_transition(
            previous_available_remaining_ratio="0.0",
            previous_buffer_entered=True,
            activity={"available": True, "remaining_ratio": "5.0", "entry_amount": 0},
            buffer_enabled=True,
        )
        self.assertFalse(recovered["buffer_entry_started"])
        reentered = panel.project_main_budget_warning_transition(
            previous_available_remaining_ratio=recovered["available_remaining_ratio"],
            previous_buffer_entered=recovered["buffer_entered"],
            activity={"available": True, "remaining_ratio": "0.0", "entry_amount": 10_000_000},
            buffer_enabled=True,
        )
        self.assertTrue(reentered["buffer_entry_started"])

    def test_budget_warning_disabled_buffer_never_tracks_entry(self) -> None:
        transition = panel.project_main_budget_warning_transition(
            previous_available_remaining_ratio="10.0",
            previous_buffer_entered=False,
            activity={"available": True, "remaining_ratio": "0.0", "entry_amount": 20_000_000},
            buffer_enabled=False,
        )

        self.assertIsNone(transition["buffer_entered"])
        self.assertFalse(transition["buffer_entry_started"])

    def test_failed_recovery_blocks_consumption_before_runtime_projection(self) -> None:
        identity = SimpleNamespace()
        context = SimpleNamespace(
            identity=identity,
            account_status="FAILED",
            stocks=(),
        )
        window = SimpleNamespace(
            selected_account_no=lambda: "12345678",
            _production_recovery_identity=identity,
        )
        with (
            patch.object(
                panel.production_recovery_registry,
                "snapshot",
                return_value=context,
            ),
            patch.object(
                panel,
                "project_account_auto_trade_budget_consumption",
            ) as projection,
        ):
            result = panel.collect_main_account_budget_consumption(window)

        self.assertFalse(result["available"])
        self.assertEqual("current account Recovery is not complete", result["reason"])
        projection.assert_not_called()

    def test_completed_recovery_projects_valid_empty_runtime_as_zero(self) -> None:
        account = "12345678"
        session = "LOGIN-SESSION-1"
        identity = SimpleNamespace(
            account_no=account,
            login_session_id=session,
            trading_day=datetime.now().date().isoformat(),
        )
        context = SimpleNamespace(
            identity=identity,
            account_status=panel.ACCOUNT_COMPLETED,
            stocks=(),
        )
        window = SimpleNamespace(
            selected_account_no=lambda: account,
            _production_recovery_identity=identity,
            kiwoom_api=SimpleNamespace(login_session_id=lambda: session),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            positions_path = root / "positions.json"
            queue_path = root / "order_queue.json"
            positions_path.write_text(
                '{"version": 1, "positions": []}',
                encoding="utf-8",
            )
            queue_path.write_text(
                '{"version": 1, "orders": []}',
                encoding="utf-8",
            )
            with (
                patch.object(
                    panel.production_recovery_registry,
                    "snapshot",
                    return_value=context,
                ),
                patch.object(panel, "POSITIONS_PATH", positions_path),
                patch.object(panel, "ORDER_QUEUE_PATH", queue_path),
            ):
                result = panel.collect_main_account_budget_consumption(window)

        self.assertTrue(result["available"])
        self.assertEqual(0, result["holding_cost"])
        self.assertEqual(0, result["open_buy_reservation"])
        self.assertEqual(0, result["consumed_amount"])

    def test_disconnect_reverts_confirmed_amounts_to_dash(self) -> None:
        window = SimpleNamespace(
            _main_budget_orderable_valid=True,
            budget_total_label=_TextTarget(),
            budget_available_label=_TextTarget(),
            budget_reserve_label=_TextTarget(),
            budget_available_percent_edit=_TextTarget(),
            budget_buffer_percent_edit=_TextTarget(),
            budget_available_percent_suffix_label=_TextTarget(),
            budget_buffer_percent_suffix_label=_TextTarget(),
        )
        summary = panel.project_system_budget_amounts(400_000_000, 70)
        with patch.object(panel, "collect_main_budget_summary", return_value=summary):
            panel.update_main_budget_panel(window)
            self.assertEqual("280,000,000", window.budget_available_label.text)
            window._main_budget_orderable_valid = None
            panel.update_main_budget_panel(window)

        self.assertEqual("-", window.budget_available_label.text)
        self.assertEqual("-", window.budget_reserve_label.text)

    def test_disabled_buffer_hides_available_amount_until_account_confirmation(self) -> None:
        window = SimpleNamespace(
            _main_budget_orderable_valid=False,
            budget_total_label=_TextTarget(),
            budget_available_label=_TextTarget(),
            budget_reserve_label=_TextTarget(),
            budget_available_percent_edit=_TextTarget(),
            budget_buffer_percent_edit=_TextTarget(),
            budget_available_percent_suffix_label=_TextTarget(),
            budget_buffer_percent_suffix_label=_TextTarget(),
            budget_available_remaining_label=_TextTarget(),
            budget_buffer_entry_label=_TextTarget(),
            budget_available_remaining_ratio_label=_TextTarget(),
            budget_buffer_entry_ratio_label=_TextTarget(),
        )
        summary = panel.project_system_budget_amounts(400_000_000, 100)
        with (
            patch.object(panel, "collect_main_budget_summary", return_value=summary),
            patch.object(
                panel,
                "collect_main_account_budget_consumption",
                return_value={"available": True, "consumed_amount": 0},
            ),
        ):
            panel.update_main_budget_panel(window)
            self.assertEqual("-", window.budget_available_label.text)
            self.assertEqual("-", window.budget_reserve_label.text)
            self.assertEqual("-", window.budget_available_percent_edit.text)
            self.assertEqual("-", window.budget_buffer_percent_edit.text)
            self.assertEqual("-", window.budget_available_remaining_label.text)
            self.assertEqual("-", window.budget_available_remaining_ratio_label.text)
            self.assertEqual("-", window.budget_buffer_entry_label.text)
            self.assertEqual("-", window.budget_buffer_entry_ratio_label.text)
            window._main_budget_orderable_valid = True
            panel.update_main_budget_panel(window)

        self.assertEqual("400,000,000", window.budget_available_label.text)
        self.assertEqual("-", window.budget_reserve_label.text)
        self.assertEqual("400,000,000", window.budget_available_remaining_label.text)
        self.assertEqual("100.0%", window.budget_available_remaining_ratio_label.text)
        self.assertEqual("-", window.budget_buffer_entry_label.text)
        self.assertEqual("-", window.budget_buffer_entry_ratio_label.text)

    def test_buffer_enabled_is_based_on_configured_buffer_percent(self) -> None:
        summary = panel.project_system_budget_amounts(0, 90)

        self.assertEqual(summary["available_budget"], summary["total_budget"])
        self.assertTrue(summary["buffer_enabled"])
        self.assertFalse(
            panel.project_system_budget_amounts(400_000_000, 100)["buffer_enabled"]
        )

    def test_budget_box_uses_equal_right_aligned_maximum_value_columns(self) -> None:
        from gui_windows import MainWindow

        class _BudgetHost:
            def __init__(self) -> None:
                self._account_memo_settings = _MemorySettings()
                self.buffer_response_click_count = 0
                self.budget_total_label = QLabel("0")
                self.budget_available_label = QLabel("0")
                self.budget_reserve_label = QLabel("0")
                self.budget_used_label = QLabel("0")
                self.budget_usage_rate_label = QLabel("-")
                self.budget_routine_count_label = QLabel("0")
                self.budget_stock_count_label = QLabel("0")
                self.budget_status_label = QLabel("확인 전")

            def _commit_main_budget_percent(self, _source: str) -> None:
                return None

            def update_budget_panel(self) -> None:
                return None

            def main_budget_warning_enabled(self) -> bool:
                return MainWindow.main_budget_warning_enabled(self)

            def set_main_budget_warning_enabled(self, enabled: bool) -> None:
                MainWindow.set_main_budget_warning_enabled(self, enabled)

            def on_main_budget_buffer_response_entry_clicked(self) -> None:
                self.buffer_response_click_count += 1

        host = _BudgetHost()
        box = MainWindow._create_budget_status_box(host)
        box.show()
        self.app.processEvents()
        labels = (
            host.budget_total_label,
            host.budget_available_label,
            host.budget_reserve_label,
        )
        widths = {label.width() for label in labels}
        expected_minimum = labels[0].fontMetrics().horizontalAdvance("9,999,999,999")

        self.assertEqual(1, len(widths))
        self.assertGreaterEqual(labels[0].width(), expected_minimum)
        for label in labels:
            self.assertTrue(label.alignment() & Qt.AlignRight)
            self.assertTrue(label.alignment() & Qt.AlignVCenter)
        self.assertEqual(3, host.budget_available_percent_edit.maxLength())
        self.assertEqual(3, host.budget_buffer_percent_edit.maxLength())
        for editor in (
            host.budget_available_percent_edit,
            host.budget_buffer_percent_edit,
        ):
            self.assertTrue(editor.alignment() & Qt.AlignRight)
            self.assertTrue(editor.alignment() & Qt.AlignVCenter)
            style = editor.styleSheet().lower()
            self.assertIn("border: none", style)
            self.assertIn("outline: none", style)
            self.assertIn("background: transparent", style)
            self.assertIn("padding: 0", style)

        activity_amount_labels = (
            host.budget_available_remaining_label,
            host.budget_buffer_entry_label,
        )
        self.assertEqual(
            {labels[0].width()},
            {label.width() for label in activity_amount_labels},
        )
        for label in activity_amount_labels:
            self.assertEqual(Qt.AlignCenter, label.alignment())
            panel.set_metric_value_text(label, "9,999,999,999")
            self.assertEqual(Qt.AlignRight | Qt.AlignVCenter, label.alignment())
            self.assertGreaterEqual(label.width(), expected_minimum)

        ratio_labels = (
            host.budget_available_remaining_ratio_label,
            host.budget_buffer_entry_ratio_label,
        )
        expected_ratio_minimum = ratio_labels[0].fontMetrics().horizontalAdvance(
            "100.0%"
        )
        self.assertEqual(1, len({label.width() for label in ratio_labels}))
        for label in ratio_labels:
            self.assertGreaterEqual(label.width(), expected_ratio_minimum)
            self.assertEqual(Qt.AlignCenter, label.alignment())
            panel.set_metric_value_text(label, "108.3%")
            self.assertEqual(Qt.AlignRight | Qt.AlignVCenter, label.alignment())
            self.assertLessEqual(
                label.fontMetrics().horizontalAdvance(label.text()),
                label.contentsRect().width(),
            )

        self.assertEqual(labels[1].geometry().right(), labels[2].geometry().right())
        self.assertEqual(labels[0].geometry().left(), labels[1].geometry().left())
        self.assertEqual(labels[0].geometry().right(), labels[1].geometry().right())
        self.assertEqual(
            host.budget_available_activity_separator_label.geometry().left(),
            host.budget_buffer_activity_separator_label.geometry().left(),
        )
        self.assertEqual(
            host.budget_available_activity_title_label.geometry().left(),
            host.budget_buffer_activity_title_label.geometry().left(),
        )
        self.assertEqual(
            host.budget_available_activity_title_label.width(),
            host.budget_buffer_activity_title_label.width(),
        )
        self.assertEqual(
            host.budget_available_remaining_label.geometry().right(),
            host.budget_buffer_entry_label.geometry().right(),
        )
        self.assertEqual(
            host.budget_available_remaining_ratio_label.geometry().right(),
            host.budget_buffer_entry_ratio_label.geometry().right(),
        )
        self.assertEqual("|", host.budget_warning_separator_label.text())
        self.assertEqual("경고 ON", host.budget_warning_toggle_button.text())
        self.assertFalse(host.budget_warning_toggle_button.isCheckable())
        self.assertIn("border: 1px solid #16a34a", host.budget_warning_toggle_button.styleSheet().lower())
        self.assertIn("border-radius: 4px", host.budget_warning_toggle_button.styleSheet().lower())
        self.assertIn("background-color: transparent", host.budget_warning_toggle_button.styleSheet().lower())
        self.assertEqual(3, host.budget_warning_row_widget.layout().count())
        total_row_layout = box.layout().itemAt(0).layout()
        detail_grid_layout = box.layout().itemAt(1).layout()
        self.assertGreaterEqual(total_row_layout.indexOf(host.budget_warning_row_widget), 0)
        self.assertEqual(-1, detail_grid_layout.indexOf(host.budget_warning_row_widget))
        badge_metrics = host.budget_warning_toggle_button.fontMetrics()
        expected_badge_width = max(
            badge_metrics.horizontalAdvance("경고 ON"),
            badge_metrics.horizontalAdvance("경고 OFF"),
        ) + 16
        self.assertGreaterEqual(
            host.budget_warning_toggle_button.width(),
            expected_badge_width,
        )
        self.assertGreaterEqual(
            host.budget_warning_toggle_button.height(),
            badge_metrics.height() + 2,
        )
        self.assertLess(host.budget_warning_toggle_button.height(), 30)
        self.assertGreater(
            host.budget_warning_row_widget.geometry().left(),
            host.budget_total_label.geometry().right(),
        )
        total_geometry = host.budget_total_label.geometry()
        badge_geometry = host.budget_warning_toggle_button.geometry()
        detail_geometries = tuple(
            widget.geometry()
            for widget in (
                host.budget_available_label,
                host.budget_reserve_label,
                host.budget_available_activity_separator_label,
                host.budget_buffer_activity_separator_label,
                host.budget_available_remaining_label,
                host.budget_buffer_entry_label,
                host.budget_available_remaining_ratio_label,
                host.budget_buffer_entry_ratio_label,
            )
        )
        QTest.mouseClick(host.budget_warning_toggle_button, Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual("경고 ON", host.budget_warning_toggle_button.text())
        self.assertTrue(host.main_budget_warning_enabled())
        self.assertEqual([], host._account_memo_settings.set_calls)

        QTest.mouseDClick(host.budget_warning_toggle_button, Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual("경고 OFF", host.budget_warning_toggle_button.text())
        self.assertFalse(host.main_budget_warning_enabled())
        self.assertEqual(1, len(host._account_memo_settings.set_calls))
        self.assertIn("border: 1px solid #111827", host.budget_warning_toggle_button.styleSheet().lower())
        self.assertEqual(total_geometry, host.budget_total_label.geometry())
        self.assertEqual(badge_geometry, host.budget_warning_toggle_button.geometry())
        self.assertEqual(
            detail_geometries,
            tuple(
                widget.geometry()
                for widget in (
                    host.budget_available_label,
                    host.budget_reserve_label,
                    host.budget_available_activity_separator_label,
                    host.budget_buffer_activity_separator_label,
                    host.budget_available_remaining_label,
                    host.budget_buffer_entry_label,
                    host.budget_available_remaining_ratio_label,
                    host.budget_buffer_entry_ratio_label,
                )
            ),
        )
        QTest.mouseDClick(host.budget_warning_toggle_button, Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual("경고 ON", host.budget_warning_toggle_button.text())
        self.assertTrue(host.main_budget_warning_enabled())
        self.assertEqual(2, len(host._account_memo_settings.set_calls))
        self.assertEqual(total_geometry, host.budget_total_label.geometry())
        self.assertEqual(badge_geometry, host.budget_warning_toggle_button.geometry())
        response_geometry = host.budget_buffer_response_button.geometry()
        self.assertEqual("완충대응", host.budget_buffer_response_button.text())
        self.assertEqual(
            host.budget_warning_toggle_button.height(),
            host.budget_buffer_response_button.height(),
        )
        self.assertGreater(
            host.budget_buffer_response_button.geometry().left(),
            host.budget_warning_toggle_button.geometry().right(),
        )
        self.assertIn(
            "border-radius: 4px",
            host.budget_buffer_response_button.styleSheet().lower(),
        )
        response_metrics = host.budget_buffer_response_button.fontMetrics()
        self.assertGreaterEqual(
            host.budget_buffer_response_button.width(),
            response_metrics.horizontalAdvance("완충대응") + 16,
        )
        QTest.mouseClick(host.budget_buffer_response_button, Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual(1, host.buffer_response_click_count)
        self.assertEqual(response_geometry, host.budget_buffer_response_button.geometry())
        self.assertEqual(total_geometry, host.budget_total_label.geometry())
        self.assertEqual(
            detail_geometries,
            tuple(
                widget.geometry()
                for widget in (
                    host.budget_available_label,
                    host.budget_reserve_label,
                    host.budget_available_activity_separator_label,
                    host.budget_buffer_activity_separator_label,
                    host.budget_available_remaining_label,
                    host.budget_buffer_entry_label,
                    host.budget_available_remaining_ratio_label,
                    host.budget_buffer_entry_ratio_label,
                )
            ),
        )
        box.deleteLater()

    def test_buffer_response_surface_is_nonmodal_memory_only_ui(self) -> None:
        import gui_windows

        owner = QWidget()
        surface = gui_windows._BufferResponseSettingsSurface(owner)
        try:
            surface.show()
            self.app.processEvents()
            self.assertFalse(surface.isModal())
            self.assertEqual(
                gui_windows._BufferResponseSettingsSurface.MODE_UNIFIED,
                surface.application_mode(),
            )
            self.assertEqual(560, surface.width())
            self.assertEqual("일괄적용", surface.unified_checkbox.text())
            self.assertEqual("손익별 적용", surface.segmented_checkbox.text())
            self.assertEqual("", surface.unified_strategy_title_label.text())
            self.assertEqual("▪ 수익", surface.profit_strategy_title_label.text())
            self.assertEqual("▪ 손실", surface.loss_strategy_title_label.text())
            self.assertEqual(
                0,
                surface.unified_strategy_title_label.contentsMargins().left(),
            )
            self.assertEqual(
                12,
                surface.profit_strategy_title_label.contentsMargins().left(),
            )
            self.assertEqual(
                12,
                surface.loss_strategy_title_label.contentsMargins().left(),
            )
            root_layout = surface.layout()
            self.assertEqual(34, root_layout.contentsMargins().left())
            self.assertEqual(34, root_layout.contentsMargins().right())
            self.assertEqual(
                12,
                surface.unified_strategy_row.layout().contentsMargins().left(),
            )
            self.assertEqual(
                12,
                surface.profit_strategy_row.layout().contentsMargins().left(),
            )
            self.assertEqual(
                12,
                surface.loss_strategy_row.layout().contentsMargins().left(),
            )
            self.assertFalse(hasattr(surface, "contract_label"))
            self.assertEqual(360, surface.height())
            self.assertLess(
                root_layout.indexOf(surface.unified_checkbox),
                root_layout.indexOf(surface.unified_strategy_row),
            )
            self.assertLess(
                root_layout.indexOf(surface.unified_strategy_row),
                root_layout.indexOf(surface.segmented_checkbox),
            )
            self.assertLess(
                root_layout.indexOf(surface.segmented_checkbox),
                root_layout.indexOf(surface.profit_strategy_row),
            )
            self.assertLess(
                root_layout.indexOf(surface.profit_strategy_row),
                root_layout.indexOf(surface.loss_strategy_row),
            )
            self.assertTrue(surface.unified_checkbox.isChecked())
            self.assertFalse(surface.segmented_checkbox.isChecked())
            self.assertTrue(surface.unified_checkbox.isEnabled())
            self.assertTrue(surface.segmented_checkbox.isEnabled())
            self.assertTrue(surface.unified_strategy_row.isVisible())
            self.assertTrue(surface.profit_strategy_row.isVisible())
            self.assertTrue(surface.loss_strategy_row.isVisible())
            self.assertTrue(surface.unified_strategy_row.isEnabled())
            self.assertFalse(surface.profit_strategy_row.isEnabled())
            self.assertFalse(surface.loss_strategy_row.isEnabled())
            self.assertEqual(1, len(surface.strategy_rows["unified"]))
            surface.strategy_rows["unified"][0][0].setCurrentText("투입금액")

            surface.segmented_checkbox.click()
            self.app.processEvents()
            self.assertEqual(
                gui_windows._BufferResponseSettingsSurface.MODE_SEGMENTED,
                surface.application_mode(),
            )
            self.assertFalse(surface.unified_checkbox.isChecked())
            self.assertTrue(surface.segmented_checkbox.isChecked())
            self.assertTrue(surface.unified_checkbox.isEnabled())
            self.assertTrue(surface.segmented_checkbox.isEnabled())
            self.assertTrue(surface.unified_strategy_row.isVisible())
            self.assertTrue(surface.profit_strategy_row.isVisible())
            self.assertTrue(surface.loss_strategy_row.isVisible())
            self.assertFalse(surface.unified_strategy_row.isEnabled())
            self.assertTrue(surface.profit_strategy_row.isEnabled())
            self.assertTrue(surface.loss_strategy_row.isEnabled())
            self.assertEqual(1, len(surface.strategy_rows["profit"]))
            self.assertEqual(1, len(surface.strategy_rows["loss"]))
            surface.strategy_rows["profit"][0][1].setCurrentText("낮은순")
            self.assertEqual(
                surface.profit_strategy_title_label.width(),
                surface.loss_strategy_title_label.width(),
            )
            self.assertEqual(
                surface.unified_strategy_title_label.width(),
                surface.profit_strategy_title_label.width(),
            )
            unified_factor = surface.strategy_rows["unified"][0][0]
            profit_factor = surface.strategy_rows["profit"][0][0]
            loss_factor = surface.strategy_rows["loss"][0][0]
            self.assertEqual(
                profit_factor.mapTo(surface, profit_factor.rect().topLeft()).x(),
                loss_factor.mapTo(surface, loss_factor.rect().topLeft()).x(),
            )
            self.assertEqual(
                unified_factor.mapTo(surface, unified_factor.rect().topLeft()).x(),
                profit_factor.mapTo(surface, profit_factor.rect().topLeft()).x(),
            )

            surface.segmented_checkbox.click()
            self.app.processEvents()
            self.assertEqual(
                gui_windows._BufferResponseSettingsSurface.MODE_NONE,
                surface.application_mode(),
            )
            self.assertFalse(surface.unified_checkbox.isChecked())
            self.assertFalse(surface.segmented_checkbox.isChecked())
            self.assertTrue(surface.unified_checkbox.isEnabled())
            self.assertTrue(surface.segmented_checkbox.isEnabled())
            self.assertFalse(surface.unified_strategy_row.isEnabled())
            self.assertFalse(surface.profit_strategy_row.isEnabled())
            self.assertFalse(surface.loss_strategy_row.isEnabled())

            surface.unified_checkbox.click()
            self.app.processEvents()
            self.assertTrue(surface.unified_checkbox.isChecked())
            self.assertFalse(surface.segmented_checkbox.isChecked())
            self.assertTrue(surface.unified_strategy_row.isEnabled())
            self.assertFalse(surface.profit_strategy_row.isEnabled())
            self.assertFalse(surface.loss_strategy_row.isEnabled())
            self.assertEqual(
                "투입금액",
                surface.strategy_rows["unified"][0][0].currentText(),
            )
            surface.segmented_checkbox.click()
            self.app.processEvents()
            self.assertEqual(
                "낮은순",
                surface.strategy_rows["profit"][0][1].currentText(),
            )
            self.assertFalse(surface.save_button.isEnabled())

            factors = set()
            for rows in surface.strategy_rows.values():
                for factor_combo, direction_combo in rows:
                    factors.update(
                        factor_combo.itemText(index)
                        for index in range(factor_combo.count())
                    )
                    self.assertEqual(
                        list(gui_windows.BUFFER_RESPONSE_SORT_DIRECTIONS),
                        [
                            direction_combo.itemText(index)
                            for index in range(direction_combo.count())
                        ],
                    )
            self.assertEqual(
                set(gui_windows.BUFFER_RESPONSE_EVALUATION_FACTORS),
                factors,
            )
            self.assertEqual(
                {"손익비율", "손익금액", "투입금액"},
                factors,
            )
        finally:
            surface.close()
            owner.deleteLater()

    def test_buffer_response_single_filters_are_independent(self) -> None:
        import gui_windows

        owner = QWidget()
        surface = gui_windows._BufferResponseSettingsSurface(owner)
        try:
            surface.show()
            self.app.processEvents()

            unified = surface.strategy_rows["unified"][0]
            profit = surface.strategy_rows["profit"][0]
            loss = surface.strategy_rows["loss"][0]
            self.assertEqual(("손익금액", "낮은순"), (
                unified[0].currentText(), unified[1].currentText()
            ))
            self.assertEqual(("손익금액", "높은순"), (
                profit[0].currentText(), profit[1].currentText()
            ))
            self.assertEqual(("손익금액", "낮은순"), (
                loss[0].currentText(), loss[1].currentText()
            ))
            for factor_combo, direction_combo in (unified, profit, loss):
                self.assertEqual(-1, factor_combo.findText("미설정"))
                self.assertEqual(
                    list(gui_windows.BUFFER_RESPONSE_EVALUATION_FACTORS),
                    [
                        factor_combo.itemText(index)
                        for index in range(factor_combo.count())
                    ],
                )
                self.assertEqual(
                    list(gui_windows.BUFFER_RESPONSE_SORT_DIRECTIONS),
                    [
                        direction_combo.itemText(index)
                        for index in range(direction_combo.count())
                    ],
                )

            unified[0].setCurrentText("투입금액")
            unified[1].setCurrentText("높은순")
            surface.segmented_checkbox.click()
            profit[0].setCurrentText("손익비율")
            loss[0].setCurrentText("투입금액")
            self.app.processEvents()
            self.assertEqual(("투입금액", "높은순"), (
                unified[0].currentText(), unified[1].currentText()
            ))
            self.assertEqual("손익비율", profit[0].currentText())
            self.assertEqual("투입금액", loss[0].currentText())

            surface.unified_checkbox.click()
            self.app.processEvents()
            self.assertTrue(surface.unified_strategy_row.isEnabled())
            self.assertFalse(surface.profit_strategy_row.isEnabled())
            self.assertFalse(surface.loss_strategy_row.isEnabled())
            self.assertEqual(("투입금액", "높은순"), (
                unified[0].currentText(), unified[1].currentText()
            ))
            self.assertEqual("손익비율", profit[0].currentText())
            self.assertEqual("투입금액", loss[0].currentText())
            labels = {label.text() for label in surface.findChildren(QLabel)}
            self.assertNotIn("1순위", labels)
            self.assertNotIn("2순위", labels)
            self.assertNotIn("3순위", labels)
        finally:
            surface.close()
            owner.deleteLater()

    def test_buffer_response_action_badges_and_ratio_selector_are_ui_only(self) -> None:
        import gui_windows

        owner = QWidget()
        surface = gui_windows._BufferResponseSettingsSurface(owner)
        try:
            surface.show()
            self.app.processEvents()
            badges = surface.strategy_action_badges
            self.assertEqual("조기마감", badges["unified"].text())
            self.assertEqual("조기마감", badges["profit"].text())
            self.assertEqual("즉시청산", badges["loss"].text())

            QTest.mouseClick(badges["unified"], Qt.LeftButton)
            self.assertEqual("조기마감", badges["unified"].text())
            QTest.mouseDClick(badges["unified"], Qt.LeftButton)
            self.assertEqual("즉시청산", badges["unified"].text())
            QTest.mouseDClick(badges["unified"], Qt.LeftButton)
            self.assertEqual("구간마감", badges["unified"].text())
            QTest.mouseDClick(badges["unified"], Qt.LeftButton)
            self.assertEqual("조기마감", badges["unified"].text())

            inactive_profit = badges["profit"].text()
            QTest.mouseDClick(badges["profit"], Qt.LeftButton)
            self.assertEqual(inactive_profit, badges["profit"].text())
            surface.segmented_checkbox.click()
            self.app.processEvents()
            inactive_unified = badges["unified"].text()
            QTest.mouseDClick(badges["unified"], Qt.LeftButton)
            self.assertEqual(inactive_unified, badges["unified"].text())

            QTest.mouseDClick(badges["profit"], Qt.LeftButton)
            self.assertEqual("즉시청산", badges["profit"].text())
            self.assertEqual("즉시청산", badges["loss"].text())
            QTest.mouseDClick(badges["profit"], Qt.LeftButton)
            self.assertEqual("구간마감", badges["profit"].text())
            QTest.mouseDClick(badges["profit"], Qt.LeftButton)
            self.assertEqual("조기마감", badges["profit"].text())
            QTest.mouseDClick(badges["loss"], Qt.LeftButton)
            self.assertEqual("구간마감", badges["loss"].text())
            QTest.mouseDClick(badges["loss"], Qt.LeftButton)
            self.assertEqual("조기마감", badges["loss"].text())
            QTest.mouseDClick(badges["loss"], Qt.LeftButton)
            self.assertEqual("즉시청산", badges["loss"].text())

            early_text = surface.buffer_close_early_badge.text()
            immediate_text = surface.buffer_close_immediate_badge.text()
            self.assertEqual(
                "※ 구간마감설정 :",
                surface.buffer_close_title_label.text(),
            )
            self.assertEqual(
                12,
                surface.buffer_close_title_label.parentWidget()
                .layout()
                .contentsMargins()
                .left(),
            )
            QTest.mouseClick(surface.buffer_close_early_badge, Qt.LeftButton)
            QTest.mouseClick(surface.buffer_close_immediate_badge, Qt.LeftButton)
            self.assertEqual(early_text, surface.buffer_close_early_badge.text())
            self.assertEqual(
                immediate_text,
                surface.buffer_close_immediate_badge.text(),
            )

            ratio_combo = surface.buffer_close_ratio_combo
            self.assertEqual(
                [f"{percent}%" for percent in range(10, 100, 10)],
                [ratio_combo.itemText(index) for index in range(ratio_combo.count())],
            )
            self.assertEqual("80%", ratio_combo.currentText())
            self.assertEqual(Qt.ElideNone, ratio_combo.view().textElideMode())
            widest_ratio = max(
                ratio_combo.view().fontMetrics().horizontalAdvance(
                    ratio_combo.itemText(index)
                )
                for index in range(ratio_combo.count())
            )
            self.assertGreaterEqual(
                ratio_combo.view().minimumWidth(),
                widest_ratio + 20,
            )
            for ratio in ("10%", "50%", "90%"):
                ratio_combo.setCurrentText(ratio)
                self.assertEqual(ratio, ratio_combo.currentText())
            ratio_style = ratio_combo.styleSheet().replace(" ", "").lower()
            self.assertIn("border:none", ratio_style)
            self.assertIn("background:transparent", ratio_style)
            self.assertIn("qcombobox::drop-down", ratio_style)
            self.assertIn("qcombobox::down-arrow", ratio_style)
            self.assertIn("image:none", ratio_style)
            QTest.mouseClick(
                ratio_combo,
                Qt.LeftButton,
                pos=ratio_combo.rect().center(),
            )
            QTest.qWait(10)
            self.app.processEvents()
            self.assertTrue(ratio_combo.view().isVisible())
            ratio_combo.hidePopup()
        finally:
            surface.close()
            owner.deleteLater()

    def test_buffer_response_badge_uses_orange_only_while_entered(self) -> None:
        from gui_windows import MainWindow

        button = QPushButton("완충대응")
        host = SimpleNamespace(budget_buffer_response_button=button)
        MainWindow._apply_main_budget_buffer_response_badge_style(host, True)
        self.assertIn("#ea580c", button.styleSheet().lower())

        MainWindow._apply_main_budget_buffer_response_badge_style(host, False)
        self.assertNotIn("#ea580c", button.styleSheet().lower())
        button.deleteLater()

    def test_buffer_response_entry_reuses_one_open_window(self) -> None:
        from gui_windows import MainWindow

        host = QWidget()
        try:
            MainWindow.on_main_budget_buffer_response_entry_clicked(host)
            first = host._main_buffer_response_settings_surface
            self.assertTrue(first.isVisible())
            MainWindow.on_main_budget_buffer_response_entry_clicked(host)
            self.assertIs(first, host._main_buffer_response_settings_surface)
            self.assertTrue(first.isVisible())
        finally:
            surface = getattr(host, "_main_buffer_response_settings_surface", None)
            if surface is not None:
                surface.close()
            host.deleteLater()

    def test_budget_warning_preference_defaults_on_and_persists_toggle(self) -> None:
        from gui_windows import BUDGET_WARNING_SETTINGS_KEY, MainWindow

        host = SimpleNamespace(_account_memo_settings=_MemorySettings())
        self.assertTrue(MainWindow.main_budget_warning_enabled(host))

        MainWindow.set_main_budget_warning_enabled(host, False)
        self.assertFalse(MainWindow.main_budget_warning_enabled(host))
        self.assertFalse(host._account_memo_settings.values[BUDGET_WARNING_SETTINGS_KEY])

        MainWindow.set_main_budget_warning_enabled(host, True)
        self.assertTrue(MainWindow.main_budget_warning_enabled(host))

    def test_budget_warning_handler_uses_existing_toast_and_off_only_mutes(self) -> None:
        import gui_windows

        class _Host:
            def __init__(self, enabled: bool) -> None:
                self.enabled = enabled
                self._main_budget_warning_previous_available_ratio = None
                self._main_budget_warning_previous_buffer_entered = None

            def main_budget_warning_enabled(self) -> bool:
                return self.enabled

        host = _Host(enabled=True)
        with patch.object(gui_windows, "show_toast") as toast:
            gui_windows.MainWindow.handle_main_budget_warning_projection(
                host,
                activity={"available": True, "remaining_ratio": "85.0", "entry_amount": 0},
                buffer_enabled=True,
            )
            gui_windows.MainWindow.handle_main_budget_warning_projection(
                host,
                activity={"available": True, "remaining_ratio": "79.0", "entry_amount": 0},
                buffer_enabled=True,
            )
            self.assertEqual(
                "가용금액이 80% 남았습니다.",
                toast.call_args.kwargs["message"],
            )

            gui_windows.MainWindow.handle_main_budget_warning_projection(
                host,
                activity={"available": True, "remaining_ratio": "0.0", "entry_amount": 20_000_000},
                buffer_enabled=True,
            )
            self.assertEqual(
                "경고 완충금액에 진입했습니다.\n종목 강제마감 주의하세요",
                toast.call_args.kwargs["message"],
            )

        muted = _Host(enabled=False)
        with patch.object(gui_windows, "show_toast") as toast:
            gui_windows.MainWindow.handle_main_budget_warning_projection(
                muted,
                activity={"available": True, "remaining_ratio": "85.0", "entry_amount": 0},
                buffer_enabled=True,
            )
            gui_windows.MainWindow.handle_main_budget_warning_projection(
                muted,
                activity={"available": True, "remaining_ratio": "79.0", "entry_amount": 0},
                buffer_enabled=True,
            )
            toast.assert_not_called()
            self.assertEqual(
                "79.0",
                str(muted._main_budget_warning_previous_available_ratio),
            )

    def test_enter_commit_clears_selection_and_focus_without_duplicate_commit(self) -> None:
        from gui_windows import _BudgetPercentEdit

        editor = _BudgetPercentEdit()
        editor.show()
        commits: list[bool] = []

        def finish_commit() -> None:
            commits.append(True)
            editor.finish_display()

        editor.commitRequested.connect(finish_commit)
        editor.setText("90")
        editor.setFocus()
        self.app.processEvents()
        editor.selectAll()
        self.assertTrue(editor.hasSelectedText())

        QTest.keyClick(editor, Qt.Key_Return)
        self.app.processEvents()

        self.assertEqual(1, len(commits))
        self.assertFalse(editor.hasSelectedText())
        self.assertFalse(editor.hasFocus())
        editor.deleteLater()

    def test_focus_out_commit_clears_selection_and_keeps_other_editor_unfocused(self) -> None:
        from gui_windows import _BudgetPercentEdit

        parent = QWidget()
        available = _BudgetPercentEdit(parent)
        buffer = _BudgetPercentEdit(parent)
        outside = QLineEdit(parent)
        parent.show()
        commits: list[bool] = []

        def finish_commit() -> None:
            commits.append(True)
            available.finish_display()
            buffer.finish_display()

        available.commitRequested.connect(finish_commit)
        available.setText("90")
        available.setFocus()
        self.app.processEvents()
        available.selectAll()

        outside.setFocus()
        self.app.processEvents()

        self.assertEqual(1, len(commits))
        self.assertFalse(available.hasSelectedText())
        self.assertFalse(available.hasFocus())
        self.assertFalse(buffer.hasFocus())
        parent.deleteLater()

    def test_projection_refresh_does_not_focus_either_percent_editor(self) -> None:
        from gui_windows import _BudgetPercentEdit

        available = _BudgetPercentEdit()
        buffer = _BudgetPercentEdit()
        window = SimpleNamespace(
            budget_total_label=_TextTarget(),
            budget_available_label=_TextTarget(),
            budget_reserve_label=_TextTarget(),
            budget_available_percent_edit=available,
            budget_buffer_percent_edit=buffer,
            budget_available_percent_suffix_label=_TextTarget(),
            budget_buffer_percent_suffix_label=_TextTarget(),
        )
        summary = panel.project_system_budget_amounts(2_000_000, 90)
        with patch.object(panel, "collect_main_budget_summary", return_value=summary):
            panel.update_main_budget_panel(window)
        self.app.processEvents()

        self.assertFalse(available.hasFocus())
        self.assertFalse(buffer.hasFocus())
        self.assertFalse(available.hasSelectedText())
        self.assertFalse(buffer.hasSelectedText())
        available.deleteLater()
        buffer.deleteLater()

    def test_total_budget_label_opens_only_on_double_click(self) -> None:
        from gui_windows import _DoubleClickValueLabel

        label = _DoubleClickValueLabel("2,000,000")
        label.resize(140, 30)
        label.show()
        opened: list[bool] = []
        label.doubleClicked.connect(lambda: opened.append(True))

        QTest.mouseClick(label, Qt.LeftButton)
        self.assertEqual([], opened)
        QTest.mouseDClick(label, Qt.LeftButton)
        self.assertEqual([True], opened)
        label.deleteLater()

    def test_total_budget_popup_disables_percentages_without_orderable_cash(self) -> None:
        from gui_windows import _MainTotalBudgetPopup

        class _Owner(QWidget):
            def main_total_budget_rounding_enabled(self) -> bool:
                return True

            def set_main_total_budget_rounding_enabled(self, _enabled: bool) -> None:
                return None

            def current_orderable_cash_for_budget(self):
                return None

            def apply_main_total_budget_percentage(self, _percent: int) -> bool:
                return False

            def apply_main_total_budget_direct(self, _value: object) -> bool:
                return True

        owner = _Owner()
        anchor = QLabel("2,000,000", owner)
        popup = _MainTotalBudgetPopup(owner)
        with patch.object(
            __import__("gui_windows"),
            "collect_main_budget_summary",
            return_value={"total_budget": 2_000_000},
        ):
            popup.show_below(anchor)
        self.app.processEvents()

        self.assertTrue(popup.isVisible())
        self.assertTrue(popup.direct_input.isEnabled())
        self.assertEqual(13, popup.direct_input.maxLength())
        self.assertTrue(popup.rounding_toggle.isChecked())
        self.assertEqual("자릿수맞춤 ON", popup.rounding_toggle.text())
        self.assertTrue(
            all(not button.isEnabled() for button in popup.percent_buttons.values())
        )
        QTest.keyClick(popup.direct_input, Qt.Key_Escape)
        self.app.processEvents()
        self.assertFalse(popup.isVisible())
        popup.deleteLater()
        owner.deleteLater()

    def test_total_budget_popup_enables_percentages_and_external_click_closes(self) -> None:
        from gui_windows import _MainTotalBudgetPopup

        class _Owner(QWidget):
            def main_total_budget_rounding_enabled(self) -> bool:
                return False

            def set_main_total_budget_rounding_enabled(self, _enabled: bool) -> None:
                return None

            def current_orderable_cash_for_budget(self):
                return 500_000_000

            def apply_main_total_budget_percentage(self, _percent: int) -> bool:
                return False

            def apply_main_total_budget_direct(self, _value: object) -> bool:
                return False

        owner = _Owner()
        owner.resize(400, 200)
        anchor = QLabel("2,000,000", owner)
        outside = QLabel("outside", owner)
        outside.move(240, 120)
        outside.resize(100, 30)
        popup = _MainTotalBudgetPopup(owner)
        owner.show()
        with patch.object(
            __import__("gui_windows"),
            "collect_main_budget_summary",
            return_value={"total_budget": 2_000_000},
        ):
            popup.show_below(anchor)
        self.app.processEvents()

        self.assertTrue(popup.isVisible())
        self.assertTrue(
            all(button.isEnabled() for button in popup.percent_buttons.values())
        )
        self.assertEqual(
            ["100%", "90%", "80%", "70%", "60%"],
            [
                popup.percent_layout.itemAtPosition(0, column).widget().text()
                for column in range(5)
            ],
        )
        self.assertEqual(
            ["50%", "40%", "30%", "20%", "10%"],
            [
                popup.percent_layout.itemAtPosition(1, column).widget().text()
                for column in range(5)
            ],
        )
        QTest.mouseClick(outside, Qt.LeftButton)
        self.app.processEvents()
        self.assertFalse(popup.isVisible())
        popup.deleteLater()
        owner.deleteLater()

    def test_total_budget_popup_first_and_second_show_use_stable_final_geometry(self) -> None:
        from gui_windows import _MainTotalBudgetPopup

        class _Owner(QWidget):
            def __init__(self) -> None:
                super().__init__()
                self.rounding_enabled = True

            def main_total_budget_rounding_enabled(self) -> bool:
                return self.rounding_enabled

            def set_main_total_budget_rounding_enabled(self, enabled: bool) -> None:
                self.rounding_enabled = bool(enabled)

            def current_orderable_cash_for_budget(self):
                return 9_999_999_999

            def apply_main_total_budget_percentage(self, _percent: int) -> bool:
                return False

            def apply_main_total_budget_direct(self, _value: object) -> bool:
                return False

        owner = _Owner()
        anchor = QLabel("9,999,999,999", owner)
        popup = _MainTotalBudgetPopup(owner)
        with patch.object(
            __import__("gui_windows"),
            "collect_main_budget_summary",
            return_value={"total_budget": 9_999_999_999},
        ):
            popup.show_below(anchor)
            self.app.processEvents()
            first_size = popup.size()
            self.assertEqual("자릿수맞춤 ON", popup.rounding_toggle.text())
            self.assertEqual("9,999,999,999", popup.direct_input.text())
            self.assertGreaterEqual(
                popup.rounding_toggle.width(),
                popup.rounding_toggle.sizeHint().width(),
            )

            popup.hide()
            owner.rounding_enabled = False
            popup.show_below(anchor)
            self.app.processEvents()
            second_size = popup.size()

        self.assertEqual(first_size, second_size)
        self.assertEqual(popup._stable_popup_size, first_size)
        self.assertEqual("자릿수맞춤 OFF", popup.rounding_toggle.text())
        self.assertGreaterEqual(
            popup.rounding_toggle.width(),
            popup.rounding_toggle.sizeHint().width(),
        )
        self.assertEqual(popup.minimumSize(), popup.maximumSize())
        self.assertEqual(
            ["100%", "90%", "80%", "70%", "60%"],
            [
                popup.percent_layout.itemAtPosition(0, column).widget().text()
                for column in range(5)
            ],
        )
        self.assertEqual(
            ["50%", "40%", "30%", "20%", "10%"],
            [
                popup.percent_layout.itemAtPosition(1, column).widget().text()
                for column in range(5)
            ],
        )
        popup.deleteLater()
        owner.deleteLater()

    def test_orderable_cash_refresh_never_rewrites_saved_total_budget(self) -> None:
        from gui_windows import MainWindow

        class _Host:
            def __init__(self) -> None:
                self.orderable_cash = 500_000_000
                self.budget_total_label = QLabel()

            def _kiwoom_connected_for_budget(self) -> bool:
                return True

            def current_orderable_cash_for_budget(self) -> int:
                return self.orderable_cash

        host = _Host()
        with (
            patch.object(
                __import__("gui_windows"),
                "collect_main_budget_summary",
                return_value={"total_budget": 450_000_000},
            ),
            patch.object(__import__("gui_windows"), "persist_main_total_budget") as writer,
        ):
            self.assertTrue(
                MainWindow.refresh_main_budget_orderable_validation(host)
            )
            host.orderable_cash = 400_000_000
            self.assertFalse(
                MainWindow.refresh_main_budget_orderable_validation(host)
            )

        writer.assert_not_called()
        self.assertFalse(host._main_budget_orderable_valid)
        host.budget_total_label.deleteLater()

    def test_digit_alignment_toggle_uses_only_ui_settings(self) -> None:
        from gui_windows import (
            MainWindow,
            TOTAL_BUDGET_ROUNDING_SETTINGS_KEY,
        )

        host = SimpleNamespace(_account_memo_settings=_MemorySettings())
        self.assertTrue(MainWindow.main_total_budget_rounding_enabled(host))

        MainWindow.set_main_total_budget_rounding_enabled(host, False)
        self.assertFalse(MainWindow.main_total_budget_rounding_enabled(host))
        self.assertEqual(
            {TOTAL_BUDGET_ROUNDING_SETTINGS_KEY: False},
            host._account_memo_settings.values,
        )


if __name__ == "__main__":
    unittest.main()
