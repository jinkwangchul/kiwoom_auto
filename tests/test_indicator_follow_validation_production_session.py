# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import indicator_follow_validation_production_session as production_session


def _operation_policy():
    return {
        "regular_market": {
            "start_time": "09:00:00",
            "end_time": "15:20:00",
        },
        "extra_sessions": [
            {
                "enabled": True,
                "start_time": "08:00:00",
                "end_time": "08:50:00",
            },
            {
                "enabled": True,
                "start_time": "15:30:00",
                "end_time": "20:00:00",
            },
        ],
    }


class IndicatorFollowValidationProductionSessionTest(unittest.TestCase):
    def test_calculation_cache_identity_separates_market_session_and_history(self):
        identity = getattr(
            production_session,
            "production_calculation_cache_identity",
            lambda *_args, **_kwargs: None,
        )
        contract = production_session.production_session_contract(
            {"operation_mode": "SCHEDULED"},
            {"status": "STOPPED"},
            _operation_policy(),
        )
        rows = [
            {"time": "20260921090000", "close": 100},
            {"time": "20260921090100", "close": 101},
        ]
        base = identity("005930", "005930", 5, contract, rows, 100, "REQ-1")
        self.assertIsNotNone(base)
        self.assertNotEqual(base, identity("005930", "ALT_SOURCE", 5, contract, rows, 100, "REQ-1"))
        self.assertNotEqual(base, identity("005930", "005930", 3, contract, rows, 100, "REQ-1"))
        self.assertNotEqual(base, identity("005930", "005930", 5, contract, rows, 200, "REQ-1"))
        self.assertNotEqual(base, identity("005930", "005930", 5, contract, rows[1:], 100, "REQ-1"))
        ats_contract = dict(contract, session_windows=[
            *contract["session_windows"],
            {"name": "extra2", "start_time": "15:40:00", "end_time": "20:00:00"},
        ])
        self.assertNotEqual(base, identity("005930", "005930", 5, ats_contract, rows, 100, "REQ-1"))
        selected_contract = dict(contract, selected_ats=["extra1"])
        self.assertNotEqual(base, identity("005930", "005930", 5, selected_contract, rows, 100, "REQ-1"))

    def test_production_minute_plan_blocks_a_target_above_the_production_cap(self):
        plan = getattr(production_session, "production_minute_source_plan", lambda *_: None)(
            240,
            900,
            0,
        )

        self.assertIsNotNone(plan)
        self.assertEqual("CANDLE_WARMUP_LIMIT_EXCEEDED", plan["reason"])
        self.assertFalse(plan["ready"])
        self.assertEqual(237_600, plan["requested_source_count"])
        self.assertEqual(200_000, plan["maximum_source_count"])

    def test_production_minute_projection_uses_session_anchors_and_closed_bars(self):
        policy = _operation_policy()
        policy["extra_sessions"][1]["start_time"] = "15:40:00"
        contract = production_session.production_session_contract(
            {"operation_mode": "CONTINUOUS"},
            {"manual_ats_selection": {"selected_sessions": ["extra2"]}},
            policy,
        )
        raw = [
            {"time": f"20260921{hour:02d}{minute:02d}00", "open": 100 + minute,
             "high": 101 + minute, "low": 99 + minute, "close": 100 + minute,
             "volume": 1}
            for hour, minutes in ((9, range(6)), (15, range(40, 46)))
            for minute in minutes
        ]
        raw.append({"time": "20260921153900", "open": 1, "high": 1,
                    "low": 1, "close": 1, "volume": 1})
        project = getattr(
            production_session,
            "project_production_calculation_candles",
            lambda *_args, **_kwargs: None,
        )
        result = project(raw, 3, contract, as_of=datetime.fromisoformat("2026-09-21T16:00:00+09:00"))

        self.assertIsNotNone(result)
        self.assertEqual(
            ["20260921090000", "20260921090300", "20260921154000", "20260921154300"],
            [candle["time"] for candle in result["candles"]],
        )
        self.assertTrue(all(candle["is_complete"] for candle in result["candles"]))
        self.assertEqual("FORMING_BAR_PARITY_NOT_VERIFIED", result["forming_bar_status"])

    def test_pure_contract_projects_runtime_ats_and_session_windows(self):
        report = production_session.production_session_contract(
            {"operation_mode": "CONTINUOUS"},
            {
                "manual_ats_selection": {
                    "selected_sessions": ["extra2", "extra1"],
                },
            },
            _operation_policy(),
        )

        self.assertEqual(production_session.PRODUCTION_SESSION_READY, report["status"])
        self.assertEqual("READY", report["readiness"])
        self.assertTrue(report["ready"])
        self.assertTrue(report["production_equivalent"])
        self.assertEqual(["extra1", "extra2"], report["selected_ats"])
        self.assertEqual("runtime", report["selection_source"])
        self.assertEqual(
            ["regular", "extra1", "extra2"],
            [window["name"] for window in report["session_windows"]],
        )
        json.dumps(report)

    def test_invalid_pure_contract_is_explicitly_unavailable(self):
        report = production_session.production_session_contract(
            None,
            {},
            _operation_policy(),
        )

        self.assertEqual(
            production_session.PRODUCTION_SESSION_UNAVAILABLE,
            report["status"],
        )
        self.assertEqual("UNAVAILABLE", report["readiness"])
        self.assertFalse(report["ready"])
        self.assertFalse(report["production_equivalent"])
        self.assertEqual([], report["session_windows"])

    def test_incomplete_operation_policy_does_not_guess_regular_session(self):
        policy = {"extra_sessions": _operation_policy()["extra_sessions"]}
        report = production_session.production_session_contract(
            {"operation_mode": "CONTINUOUS"},
            {"manual_ats_selection": {"selected_sessions": ["extra1"]}},
            policy,
        )
        self.assertEqual(production_session.PRODUCTION_SESSION_UNAVAILABLE, report["status"])
        self.assertFalse(report["ready"])

    def test_missing_operation_mode_does_not_default_to_scheduled(self):
        report = production_session.production_session_contract(
            {"name": "Samsung"}, {"status": "STOPPED"}, _operation_policy(),
        )
        self.assertEqual(production_session.PRODUCTION_SESSION_UNAVAILABLE, report["status"])
        self.assertFalse(report["ready"])

    def test_selected_ats_without_corresponding_policy_window_fails_closed(self):
        policy = _operation_policy()
        policy["extra_sessions"] = policy["extra_sessions"][:1]
        report = production_session.production_session_contract(
            {"operation_mode": "CONTINUOUS"},
            {"manual_ats_selection": {"selected_sessions": ["extra2"]}},
            policy,
        )
        self.assertEqual(production_session.PRODUCTION_SESSION_UNAVAILABLE, report["status"])
        self.assertFalse(report["ready"])

    def test_stock_reader_uses_registered_path_without_creating_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stock_dir = root / "stocks" / "005930_Samsung"
            stock_dir.mkdir(parents=True)
            (stock_dir / "config.json").write_text(
                json.dumps({"operation_mode": "SCHEDULED"}),
                encoding="utf-8",
            )
            (stock_dir / "state.json").write_text(
                json.dumps({"status": "STOPPED"}),
                encoding="utf-8",
            )
            before = sorted(path.relative_to(root) for path in root.rglob("*"))

            with patch.object(
                production_session.state_policy,
                "read_operation_policy",
                return_value=_operation_policy(),
            ) as policy_reader:
                report = production_session.production_session_contract_for_stock(
                    "005930",
                    project_root=root,
                )

            self.assertEqual(
                production_session.PRODUCTION_SESSION_READY,
                report["status"],
            )
            self.assertEqual([], report["selected_ats"])
            self.assertEqual("none", report["selection_source"])
            policy_reader.assert_called_once_with()
            self.assertEqual(before, sorted(path.relative_to(root) for path in root.rglob("*")))

    def test_unregistered_stock_fails_closed_without_creating_stocks_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            report = production_session.production_session_contract_for_stock(
                "005930",
                "Samsung",
                project_root=root,
            )

            self.assertEqual(
                production_session.PRODUCTION_SESSION_UNAVAILABLE,
                report["status"],
            )
            self.assertEqual("PRODUCTION_STOCK_UNREGISTERED", report["reason"])
            self.assertFalse((root / "stocks").exists())

    def test_registered_stock_with_missing_operation_policy_does_not_use_defaults(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stock_dir = root / "stocks" / "005930_Samsung"
            stock_dir.mkdir(parents=True)
            (stock_dir / "config.json").write_text(
                json.dumps({"operation_mode": "SCHEDULED"}), encoding="utf-8"
            )
            (stock_dir / "state.json").write_text(
                json.dumps({"status": "STOPPED"}), encoding="utf-8"
            )
            with patch.object(
                production_session.state_policy,
                "OPERATION_POLICY_PATH",
                root / "operation_policy.json",
            ):
                report = production_session.production_session_contract_for_stock(
                    "005930", project_root=root,
                )
            self.assertEqual(production_session.PRODUCTION_SESSION_UNAVAILABLE, report["status"])
            self.assertFalse(report["ready"])

    def test_invalid_stock_metadata_reader_result_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stock_dir = root / "stocks" / "005930_Samsung"
            stock_dir.mkdir(parents=True)
            (stock_dir / "config.json").write_text("{}", encoding="utf-8")
            (stock_dir / "state.json").write_text("{}", encoding="utf-8")

            with patch.object(
                production_session.runtime_io,
                "read_json_dict",
                side_effect=[{"operation_mode": "SCHEDULED"}, {}],
            ) as json_reader:
                report = production_session.production_session_contract_for_stock(
                    "005930",
                    project_root=root,
                )

            self.assertEqual(
                production_session.PRODUCTION_SESSION_UNAVAILABLE,
                report["status"],
            )
            self.assertEqual("PRODUCTION_STOCK_METADATA_INVALID", report["reason"])
            self.assertEqual(2, json_reader.call_count)


    def test_production_minute_retry_scales_from_observed_projection_density(self):
        plan = production_session.production_minute_source_retry_plan(
            4620,
            897,
            1400,
        )
        self.assertFalse(plan["ready"])
        self.assertTrue(plan["retry"])
        self.assertGreater(plan["requested_source_count"], 4620)
        self.assertLessEqual(
            plan["requested_source_count"],
            plan["maximum_source_count"],
        )

        ready = production_session.production_minute_source_retry_plan(
            plan["requested_source_count"],
            1400,
            1400,
        )
        self.assertTrue(ready["ready"])
        self.assertFalse(ready["retry"])

    def test_production_minute_retry_fails_closed_at_production_cap(self):
        plan = production_session.production_minute_source_retry_plan(
            200_000,
            1000,
            1400,
        )
        self.assertFalse(plan["ready"])
        self.assertFalse(plan["retry"])
        self.assertEqual("FILTER_SIGNAL_BACKTEST_BLOCKED", plan["status"])
        self.assertEqual(
            "PRODUCTION_SOURCE_HISTORY_INSUFFICIENT",
            plan["reason"],
        )

    def test_production_minute_retry_doubles_when_projection_is_empty(self):
        plan = production_session.production_minute_source_retry_plan(
            1000,
            0,
            1400,
        )
        self.assertTrue(plan["retry"])
        self.assertEqual(2000, plan["requested_source_count"])


if __name__ == "__main__":
    unittest.main()
