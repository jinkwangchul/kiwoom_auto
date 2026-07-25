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
    manual_ats_runtime_selected_keys,
    reset_manual_ats_runtime_selections,
    reset_expired_manual_ats_runtime_selections,
    write_manual_ats_runtime_selection,
)
from gui_ats_utils import manual_ats_market_day_closed
from state_policy import manual_extra_session_enabled_now


class ManualAtsRuntimeTest(unittest.TestCase):
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

    def test_selection_requires_same_trade_date_and_program_session(self) -> None:
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
        self.assertEqual(
            (),
            manual_ats_runtime_selected_keys(
                state,
                now_dt=now,
                program_session_id="session-b",
            ),
        )
        self.assertEqual(
            (),
            manual_ats_runtime_selected_keys(
                state,
                now_dt=datetime(2026, 7, 26, 8, 30, tzinfo=timezone.utc),
                program_session_id="session-a",
            ),
        )

    def test_runtime_write_and_reset_do_not_change_config_or_operation_mode(self) -> None:
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

            result = reset_manual_ats_runtime_selections(Path(temp) / "stocks")
            self.assertEqual({"cleared": 1, "failed": 0}, result)
            reset_state = json.loads((stock / "state.json").read_text(encoding="utf-8"))
            self.assertNotIn(MANUAL_ATS_SELECTION_KEY, reset_state)
            self.assertEqual("RUNNING", reset_state["status"])
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

    def test_market_close_clears_selection_without_changing_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock = Path(temp) / "stocks" / "005930_삼성전자"
            stock.mkdir(parents=True)
            (stock / "state.json").write_text(
                json.dumps(
                    {
                        "status": "RUNNING",
                        MANUAL_ATS_SELECTION_KEY: {
                            "selected_sessions": ["extra1"],
                            "trade_date": "2026-07-25",
                            "program_session_id": "session-a",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = reset_expired_manual_ats_runtime_selections(
                Path(temp) / "stocks",
                now_dt=datetime(2026, 7, 25, 20, 0, tzinfo=timezone.utc),
                market_closed=True,
            )
            state = json.loads((stock / "state.json").read_text(encoding="utf-8"))
        self.assertEqual({"cleared": 1, "failed": 0}, result)
        self.assertEqual("RUNNING", state["status"])
        self.assertNotIn(MANUAL_ATS_SELECTION_KEY, state)

    def test_market_close_uses_latest_environment_session_end(self) -> None:
        policy = {
            "regular_market": {
                "start_time": "09:00:00",
                "end_time": "15:20:00",
            },
            "extra_sessions": [
                {
                    "name": "장전프리",
                    "start_time": "08:00:00",
                    "end_time": "08:50:00",
                },
                {
                    "name": "마감후NXT",
                    "start_time": "15:30:00",
                    "end_time": "19:50:00",
                },
            ],
        }
        with patch("gui_ats_utils.read_operation_policy", return_value=policy):
            self.assertFalse(
                manual_ats_market_day_closed(
                    datetime(2026, 7, 25, 19, 49, 59, tzinfo=timezone.utc)
                )
            )
            self.assertTrue(
                manual_ats_market_day_closed(
                    datetime(2026, 7, 25, 19, 50, 0, tzinfo=timezone.utc)
                )
            )


if __name__ == "__main__":
    unittest.main()
