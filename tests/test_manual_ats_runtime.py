from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from manual_ats_runtime import (
    MANUAL_ATS_SELECTION_KEY,
    clear_manual_ats_runtime_selection,
    manual_ats_runtime_execution_method,
    manual_ats_runtime_execution_method_result,
    manual_ats_runtime_selected_keys,
    write_manual_ats_runtime_selection,
)
from gui_ats_utils import (
    manual_ats_active_now,
    manual_ats_enabled_labels,
)
from state_policy import manual_extra_session_enabled_now


class ManualAtsRuntimeTest(unittest.TestCase):
    def test_legacy_execution_method_defaults_to_routine_without_mutation(self) -> None:
        state = {MANUAL_ATS_SELECTION_KEY: {"selected_sessions": ["extra1"]}}
        before = json.dumps(state, sort_keys=True)
        self.assertEqual("ROUTINE", manual_ats_runtime_execution_method(state))
        self.assertEqual(before, json.dumps(state, sort_keys=True))

    def test_execution_methods_round_trip_and_session_only_write_preserves_method(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock = Path(temp)
            state_path = stock / "state.json"
            state_path.write_text('{"status":"STOPPED"}', encoding="utf-8")
            for method in ("ROUTINE", "MARKET", "CURRENT_PRICE"):
                with self.subTest(method=method):
                    self.assertTrue(
                        write_manual_ats_runtime_selection(
                            stock,
                            ["extra1"],
                            execution_method=method,
                        )
                    )
                    saved = json.loads(state_path.read_text(encoding="utf-8"))
                    self.assertEqual(method, manual_ats_runtime_execution_method(saved))
                    self.assertTrue(
                        write_manual_ats_runtime_selection(stock, ["extra2"])
                    )
                    preserved = json.loads(state_path.read_text(encoding="utf-8"))
                    self.assertEqual(method, manual_ats_runtime_execution_method(preserved))
                    self.assertEqual(("extra2",), manual_ats_runtime_selected_keys(preserved))

    def test_invalid_explicit_execution_method_is_not_silently_defaulted(self) -> None:
        result = manual_ats_runtime_execution_method_result(
            {MANUAL_ATS_SELECTION_KEY: {"execution_method": "BROKEN_VALUE"}}
        )
        self.assertIs(result["ok"], False)
        self.assertIsNone(result["execution_method"])
        self.assertEqual("INVALID_ATS_EXECUTION_METHOD", result["reason_code"])

    def test_environment_manual_extra_flags_do_not_apply_ats_to_stocks(self) -> None:
        self.assertFalse(
            manual_extra_session_enabled_now(
                now_dt=datetime(2026, 7, 25, 8, 30, tzinfo=timezone.utc),
                policy={
                    "manual_operation": {"use_extra_session_1": True},
                    "extra_sessions": [
                        {"start_time": "08:00:00", "end_time": "09:00:00"}
                    ],
                },
            )
        )

    def test_selection_persists_across_trade_dates_and_program_sessions(self) -> None:
        state = {
            MANUAL_ATS_SELECTION_KEY: {
                "selected_sessions": ["extra1", "extra3"],
                "trade_date": "2026-07-25",
                "program_session_id": "session-a",
            }
        }
        now = datetime(2026, 7, 25, 8, 30, tzinfo=timezone.utc)
        self.assertEqual(
            ("extra1", "extra3"),
            manual_ats_runtime_selected_keys(
                state,
                now_dt=now,
                program_session_id="session-a",
            ),
        )
        for check_now, session_id in (
            (now, "session-b"),
            (datetime(2026, 7, 26, 8, 30, tzinfo=timezone.utc), "session-a"),
            (datetime(2026, 7, 26, 8, 30, tzinfo=timezone.utc), "session-b"),
        ):
            with self.subTest(now=check_now, program_session_id=session_id):
                self.assertEqual(
                    ("extra1", "extra3"),
                    manual_ats_runtime_selected_keys(
                        state,
                        now_dt=check_now,
                        program_session_id=session_id,
                    ),
                )

    def test_empty_or_invalid_selection_has_no_active_sessions(self) -> None:
        self.assertEqual(
            (),
            manual_ats_runtime_selected_keys(
                {MANUAL_ATS_SELECTION_KEY: {"selected_sessions": []}}
            ),
        )
        self.assertEqual(
            ("extra2",),
            manual_ats_runtime_selected_keys(
                {
                    MANUAL_ATS_SELECTION_KEY: {
                        "selected_sessions": ["invalid", "extra2", "extra4"]
                    }
                }
            ),
        )

    def test_display_and_active_time_use_persisted_selection(self) -> None:
        config = {"operation_mode": "CONTINUOUS"}
        state = {
            MANUAL_ATS_SELECTION_KEY: {
                "selected_sessions": ["extra1"],
                "trade_date": "2025-01-02",
                "program_session_id": "old-session",
            }
        }
        policy = {
            "extra_sessions": [
                {
                    "enabled": True,
                    "name": "장전프리",
                    "start_time": "08:00:00",
                    "end_time": "08:50:00",
                }
            ]
        }
        with patch("gui_ats_utils.read_operation_policy", return_value=policy):
            self.assertEqual(["장전프리"], manual_ats_enabled_labels(config, state))
            self.assertTrue(
                manual_ats_active_now(
                    config,
                    state,
                    datetime(2026, 7, 26, 8, 30, tzinfo=timezone.utc),
                )
            )
            self.assertFalse(
                manual_ats_active_now(
                    config,
                    state,
                    datetime(2026, 7, 26, 9, 0, tzinfo=timezone.utc),
                )
            )

        self.assertEqual(
            [],
            manual_ats_enabled_labels({"operation_mode": "SCHEDULED"}, state),
        )

    def test_runtime_write_and_explicit_clear_do_not_change_config_or_operation_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock = Path(temp) / "stocks" / "005930_삼성전자"
            stock.mkdir(parents=True)
            config = {"operation_mode": "CONTINUOUS", "manual_ats_sessions": {"extra2": True}}
            (stock / "config.json").write_text(
                json.dumps(config, ensure_ascii=False),
                encoding="utf-8",
            )
            (stock / "state.json").write_text(
                json.dumps({"status": "RUNNING"}, ensure_ascii=False),
                encoding="utf-8",
            )
            now = datetime(2026, 7, 25, 8, 30, tzinfo=timezone.utc)

            self.assertTrue(
                write_manual_ats_runtime_selection(
                    stock,
                    {"extra1": True, "extra2": False, "extra3": True},
                    now_dt=now,
                    program_session_id="session-a",
                )
            )
            saved_state = json.loads((stock / "state.json").read_text(encoding="utf-8"))
            self.assertEqual("RUNNING", saved_state["status"])
            self.assertEqual(
                ["extra1", "extra3"],
                saved_state[MANUAL_ATS_SELECTION_KEY]["selected_sessions"],
            )
            self.assertEqual(
                config,
                json.loads((stock / "config.json").read_text(encoding="utf-8")),
            )

            self.assertTrue(clear_manual_ats_runtime_selection(stock))
            cleared_state = json.loads((stock / "state.json").read_text(encoding="utf-8"))
            self.assertNotIn(MANUAL_ATS_SELECTION_KEY, cleared_state)
            self.assertEqual("RUNNING", cleared_state["status"])
            self.assertEqual(
                config,
                json.loads((stock / "config.json").read_text(encoding="utf-8")),
            )

    def test_clear_missing_selection_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock = Path(temp)
            (stock / "state.json").write_text('{"status":"MONITORING"}', encoding="utf-8")
            self.assertTrue(clear_manual_ats_runtime_selection(stock))
            self.assertTrue(clear_manual_ats_runtime_selection(stock))

if __name__ == "__main__":
    unittest.main()
