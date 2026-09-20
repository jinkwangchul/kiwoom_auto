# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from copy import deepcopy

from indicator_follow_signal_validation_projection import (
    IndicatorFollowSignalValidationSeed,
    project_signal_validation_rules,
)

from indicator_follow_validation_timeframe import (
    TIMEFRAME_LABELS,
    normalize_validation_period_row,
    normalize_validation_timeframe,
    validation_timeframe_from_rules,
)
from routines.지표추종매매.routine_validation_contract import (
    ValidationRequest,
    ValidationSettingsSnapshot,
    ValidationStockRef,
)


class IndicatorFollowValidationTimeframeTest(unittest.TestCase):
    def test_options_are_shared_in_contract_order(self) -> None:
        self.assertEqual(
            (
                "1\ubd84",
                "3\ubd84",
                "5\ubd84",
                "10\ubd84",
                "15\ubd84",
                "30\ubd84",
                "60\ubd84",
                "120\ubd84",
                "240\ubd84",
                "\uc77c",
                "\uc8fc",
                "\ub144",
            ),
            TIMEFRAME_LABELS,
        )

    def test_legacy_numeric_minute_restores_to_labeled_identity(self) -> None:
        self.assertEqual(
            {"key": "M3", "kind": "MINUTE", "minutes": 3, "label": "3\ubd84"},
            normalize_validation_timeframe("3"),
        )
        self.assertEqual(
            {"key": "M3", "kind": "MINUTE", "minutes": 3, "label": "3\ubd84"},
            normalize_validation_timeframe("M3"),
        )

    def test_calendar_timeframes_never_acquire_fake_minutes(self) -> None:
        self.assertEqual(
            {"key": "D1", "kind": "DAY", "minutes": None, "label": "\uc77c"},
            normalize_validation_timeframe("\uc77c"),
        )
        self.assertEqual(
            {"key": "W1", "kind": "WEEK", "minutes": None, "label": "\uc8fc"},
            normalize_validation_timeframe("W1"),
        )
        self.assertEqual(
            {"key": "Y1", "kind": "YEAR", "minutes": None, "label": "\ub144"},
            normalize_validation_timeframe({"key": "Y1", "minutes": 1440}),
        )

    def test_validation_metadata_overrides_production_bar_minutes(self) -> None:
        self.assertEqual(
            {"key": "D1", "kind": "DAY", "minutes": None, "label": "\uc77c"},
            validation_timeframe_from_rules(
                {
                    "bar": {"bar_minutes": 5},
                    "validation_timeframe": {"key": "D1"},
                }
            ),
        )

    def test_validation_request_carries_period_key_without_rewriting_bar_minutes(self) -> None:
        request = ValidationRequest(
            ValidationStockRef("005930", "Samsung"),
            ValidationSettingsSnapshot(
                {
                    "bar": {"bar_minutes": 5},
                    "validation_timeframe": {"key": "D1"},
                }
            ),
            5,
        )

        self.assertEqual(5, request.timeframe_minutes)
        self.assertEqual("D1", request.timeframe_key)

    def test_legacy_validation_request_uses_explicit_request_minutes(self) -> None:
        request = ValidationRequest(
            ValidationStockRef("005930", "Samsung"),
            ValidationSettingsSnapshot({"bar": {"bar_minutes": 1}}),
            5,
        )

        self.assertEqual(5, request.timeframe_minutes)
        self.assertEqual("M5", request.timeframe_key)

    def test_period_row_normalizes_date_to_stable_fourteen_digit_time(self) -> None:
        self.assertEqual(
            {
                "\uccb4\uacb0\uc2dc\uac04": "20260918000000",
                "\uc2dc\uac00": "+70000",
                "\uace0\uac00": "+71000",
                "\uc800\uac00": "+69000",
                "\ud604\uc7ac\uac00": "+70500",
                "\uac70\ub798\ub7c9": "12345",
            },
            normalize_validation_period_row(
                {
                    "\uc77c\uc790": "20260918",
                    "\uc2dc\uac00": "+70000",
                    "\uace0\uac00": "+71000",
                    "\uc800\uac00": "+69000",
                    "\ud604\uc7ac\uac00": "+70500",
                    "\uac70\ub798\ub7c9": "12345",
                }
            ),
        )
        self.assertIsNone(normalize_validation_period_row({"\uc77c\uc790": "2026-09-18"}))


    def test_projection_preserves_explicit_period_without_current_ui(self) -> None:
        for key in ("D1", "W1", "Y1"):
            for embedded in (None, "15"):
                with self.subTest(key=key, embedded=embedded):
                    rules = {
                        "bar": {"bar_minutes": 5},
                        "validation_timeframe": {"key": key},
                    }
                    if embedded is not None:
                        rules["indicator_follow_ui_state"] = {
                            "state": {"basic": {
                                "basic_signal_interval_combo": embedded,
                            }},
                        }
                    before = deepcopy(rules)
                    projected = project_signal_validation_rules(rules)
                    self.assertEqual(key, projected["validation_timeframe"]["key"])
                    self.assertIsNone(projected["validation_timeframe"]["minutes"])
                    self.assertEqual(5, projected["bar"]["bar_minutes"])
                    self.assertEqual(before, rules)

    def test_seed_clone_and_entry_request_preserve_explicit_period(self) -> None:
        for key in ("D1", "W1", "Y1"):
            for embedded in (None, "5"):
                with self.subTest(key=key, embedded=embedded):
                    rules = {
                        "bar": {"bar_minutes": 5},
                        "validation_timeframe": {"key": key},
                    }
                    if embedded is not None:
                        rules["indicator_follow_ui_state"] = {
                            "state": {"basic": {
                                "basic_signal_interval_combo": embedded,
                            }},
                        }
                    label = normalize_validation_timeframe(key)["label"]
                    current_ui = {"basic": {"basic_signal_interval_combo": label}}
                    before_ui = deepcopy(current_ui)
                    snapshot = ValidationSettingsSnapshot(rules)
                    before_snapshot = snapshot.to_dict()
                    seed = IndicatorFollowSignalValidationSeed(snapshot, current_ui)
                    clone = IndicatorFollowSignalValidationSeed(
                        seed.settings_snapshot, seed.to_ui_state(),
                    )
                    for entry in (seed, clone):
                        request = ValidationRequest(
                            ValidationStockRef("005930", "Samsung"),
                            entry.settings_snapshot, 5,
                        )
                        self.assertEqual(key, request.timeframe_key)
                        self.assertEqual(label, entry.to_ui_state()["basic"]["basic_signal_interval_combo"])
                        self.assertEqual(5, entry.settings_snapshot.to_dict()["bar"]["bar_minutes"])
                    self.assertEqual(before_ui, current_ui)
                    self.assertEqual(before_snapshot, snapshot.to_dict())

    def test_explicit_current_ui_can_change_snapshot_timeframe(self) -> None:
        rules = {"bar": {"bar_minutes": 5}, "validation_timeframe": {"key": "D1"}}
        for chosen in ("M15", "W1", "Y1"):
            with self.subTest(chosen=chosen):
                state = {"basic": {"basic_signal_interval_combo": chosen}}
                projected = project_signal_validation_rules(rules, ui_state=state)
                self.assertEqual(chosen, projected["validation_timeframe"]["key"])
                self.assertEqual(5, projected["bar"]["bar_minutes"])

    def test_legacy_projection_still_uses_embedded_ui_without_metadata(self) -> None:
        for chosen, expected in (("15", "M15"), ("W1", "W1")):
            with self.subTest(chosen=chosen):
                rules = {
                    "bar": {"bar_minutes": 5},
                    "indicator_follow_ui_state": {"state": {"basic": {
                        "basic_signal_interval_combo": chosen,
                    }}},
                }
                projected = project_signal_validation_rules(rules)
                self.assertEqual(expected, projected["validation_timeframe"]["key"])



if __name__ == "__main__":
    unittest.main()
