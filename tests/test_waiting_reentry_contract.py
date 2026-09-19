# -*- coding: utf-8 -*-

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import gui_ats_utils as ats_utils
import gui_auto_trade_policy as policy
import gui_auto_trade_run_control as run_control
import gui_stock_data as stock_data
import routine_order_permission as order_permission
import state_policy
from gui_auto_trade_close import _persist_early_close_execution_result
from mock_validation_host import MockValidationHost
from mock_validation_ui_actions import MockValidationUIActions
from mock_validation_reference_snapshot import build_mock_reference_snapshot


class _ParticipantOwner:
    def __init__(self, codes=()):
        self.codes = {str(value) for value in codes}

    def current_session_operation_participant_stock_codes(self):
        return tuple(sorted(self.codes))

    def register_current_session_operation_participants(self, stock_codes):
        before = set(self.codes)
        self.codes.update(str(value) for value in stock_codes if str(value))
        return tuple(sorted(self.codes - before))

    def retire_current_session_operation_participants(self, stock_codes):
        before = tuple(sorted(self.codes))
        requested = tuple(str(value) for value in stock_codes if str(value))
        removed = tuple(value for value in requested if value in self.codes)
        self.codes.difference_update(requested)
        return {
            "before": before,
            "requested": requested,
            "removed": removed,
            "remaining": tuple(sorted(self.codes)),
        }


class _Window:
    def __init__(self, owner, state_path: Path | None = None):
        self._main_monitoring_auto_trade_operation_host = owner
        self.state_path = state_path

    def update_stock_status(
        self,
        _stock_dir,
        _code,
        _name,
        runtime_status,
        metadata,
        _reason,
    ):
        if self.state_path is not None:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            state["status"] = runtime_status
            state.update(metadata)
            self.state_path.write_text(
                json.dumps(state, ensure_ascii=False),
                encoding="utf-8",
            )
        return True


def _operation_policy():
    return {
        "regular_market": {
            "start_time": "09:00:00",
            "end_time": "15:20:00",
        },
        "extra_sessions": [
            {
                "enabled": True,
                "name": "PRE",
                "start_time": "08:00:00",
                "end_time": "08:55:00",
            },
            {
                "enabled": True,
                "name": "AFTER",
                "start_time": "15:30:00",
                "end_time": "19:50:00",
            },
            {
                "enabled": True,
                "name": "UNSUPPORTED",
                "start_time": "20:00:00",
                "end_time": "21:00:00",
            },
        ],
        "scheduled_operation": {
            "default_start_time": "08:50:00",
            "default_end_buy_time": "13:30:00",
        },
        "manual_operation": {
            "use_regular_market": True,
        },
    }


class StockNxtEligibilityProjectionTest(unittest.TestCase):
    def test_verified_library_nxt_projection_is_cached_and_unknown_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snapshot = SimpleNamespace(
                records=(
                    {"code": "005930", "nxt_available": True},
                    {"code": "000660", "nxt_available": False},
                    {"code": "005380", "nxt_available": None},
                )
            )
            previous_signature = stock_data._STOCK_NXT_CACHE_SIGNATURE
            previous_cache = dict(stock_data._STOCK_NXT_CACHE)
            stock_data._STOCK_NXT_CACHE_SIGNATURE = None
            stock_data._STOCK_NXT_CACHE = {}
            try:
                with patch.object(
                    stock_data,
                    "load_stock_library_snapshot",
                    return_value=snapshot,
                ) as loader:
                    self.assertIs(
                        stock_data.stock_nxt_availability("005930", root),
                        True,
                    )
                    self.assertIs(
                        stock_data.stock_nxt_availability("000660", root),
                        False,
                    )
                    self.assertIsNone(
                        stock_data.stock_nxt_availability("005380", root)
                    )
                    self.assertIsNone(
                        stock_data.stock_nxt_availability("999999", root)
                    )
                loader.assert_called_once_with(root)
            finally:
                stock_data._STOCK_NXT_CACHE_SIGNATURE = previous_signature
                stock_data._STOCK_NXT_CACHE = previous_cache


class WaitingReentryContractTest(unittest.TestCase):
    def setUp(self):
        self.owner = _ParticipantOwner()
        self.window = _Window(self.owner)
        self.policy = _operation_policy()
        self.nxt_availability_patcher = patch.object(
            policy,
            "stock_nxt_availability",
            return_value=True,
        )
        self.nxt_availability_patcher.start()
        self.addCleanup(self.nxt_availability_patcher.stop)

    def phase(self, config, state, now):
        return ats_utils.auto_trade_operation_session_phase(
            config,
            state,
            now_dt=now,
            operation_policy_reader=lambda: self.policy,
            ats_session_reader=lambda key: dict(
                self.policy["extra_sessions"][
                    {"extra1": 0, "extra2": 1, "extra3": 2}[key]
                ]
            ),
        )

    def test_configured_sessions_are_clipped_to_actual_exchange_sessions(self):
        config = {"operation_mode": "SCHEDULED"}
        state = {}

        before_exchange = self.phase(
            config,
            state,
            datetime(2026, 9, 19, 8, 55, 0),
        )
        active = self.phase(
            config,
            state,
            datetime(2026, 9, 19, 9, 0, 0),
        )
        final = self.phase(
            config,
            state,
            datetime(2026, 9, 19, 13, 30, 0),
        )

        self.assertEqual("BEFORE_FIRST_SESSION", before_exchange["phase"])
        self.assertEqual(
            ("scheduled", 9 * 3600, 13 * 3600 + 30 * 60),
            active["sessions"][0],
        )
        self.assertEqual("ACTIVE_SESSION", active["phase"])
        self.assertEqual("FINAL_SESSION_ENDED", final["phase"])

    def test_scheduled_reentry_horizon_cannot_extend_past_program_regular_end(self):
        local_policy = _operation_policy()
        local_policy["scheduled_operation"] = {
            "default_start_time": "09:00:00",
            "default_end_buy_time": "15:25:00",
        }
        config = {
            "operation_mode": "SCHEDULED",
            "start_time": "09:00:00",
            "end_buy_time": "15:25:00",
        }

        phase = ats_utils.auto_trade_operation_session_phase(
            config,
            {},
            now_dt=datetime(2026, 9, 19, 15, 20, 0),
            operation_policy_reader=lambda: local_policy,
        )

        self.assertEqual("FINAL_SESSION_ENDED", phase["phase"])
        self.assertEqual(
            ("scheduled", 9 * 3600, 15 * 3600 + 20 * 60),
            phase["sessions"][0],
        )

    def test_manual_ats_gaps_remain_reentry_windows_when_future_session_exists(self):
        config = {"operation_mode": "CONTINUOUS"}
        state = {
            "status": "WAIT_BUY",
            "manual_ats_selection": {
                "selected_sessions": ["extra1", "extra2"],
            },
        }

        pre_regular_gap = self.phase(
            config,
            state,
            datetime(2026, 9, 19, 8, 55, 0),
        )
        post_regular_gap = self.phase(
            config,
            state,
            datetime(2026, 9, 19, 15, 30, 0),
        )
        final = self.phase(
            config,
            state,
            datetime(2026, 9, 19, 19, 50, 0),
        )

        self.assertEqual("BETWEEN_SESSIONS", pre_regular_gap["phase"])
        self.assertTrue(pre_regular_gap["future_session_exists"])
        self.assertEqual("BETWEEN_SESSIONS", post_regular_gap["phase"])
        self.assertTrue(post_regular_gap["future_session_exists"])
        self.assertEqual("FINAL_SESSION_ENDED", final["phase"])

        with patch.object(ats_utils, "read_operation_policy", return_value=self.policy):
            allowed = policy.auto_trade_setting_start_target_decision(
                self.window,
                state,
                "005930",
                config=config,
                now_dt=datetime(2026, 9, 19, 15, 30, 0),
            )
            blocked = policy.auto_trade_setting_start_target_decision(
                self.window,
                state,
                "005930",
                config=config,
                now_dt=datetime(2026, 9, 19, 19, 50, 0),
            )

        self.assertTrue(allowed["allowed"])
        self.assertFalse(blocked["allowed"])
        self.assertEqual("FINAL_SESSION_ENDED", blocked["reason"])

    def test_non_nxt_ats_does_not_extend_reentry_horizon(self):
        config = {"operation_mode": "CONTINUOUS"}
        state = {
            "status": "WAIT_BUY",
            "manual_ats_selection": {"selected_sessions": ["extra2"]},
        }
        now = datetime(2026, 9, 19, 15, 30, 0)

        eligible = ats_utils.auto_trade_operation_session_phase(
            config,
            state,
            now_dt=now,
            operation_policy_reader=lambda: self.policy,
            ats_session_reader=lambda key: dict(
                self.policy["extra_sessions"][
                    {"extra1": 0, "extra2": 1, "extra3": 2}[key]
                ]
            ),
            nxt_available=True,
        )
        non_nxt = ats_utils.auto_trade_operation_session_phase(
            config,
            state,
            now_dt=now,
            operation_policy_reader=lambda: self.policy,
            ats_session_reader=lambda key: dict(
                self.policy["extra_sessions"][
                    {"extra1": 0, "extra2": 1, "extra3": 2}[key]
                ]
            ),
            nxt_available=False,
        )
        unknown = ats_utils.auto_trade_operation_session_phase(
            config,
            state,
            now_dt=now,
            operation_policy_reader=lambda: self.policy,
            ats_session_reader=lambda key: dict(
                self.policy["extra_sessions"][
                    {"extra1": 0, "extra2": 1, "extra3": 2}[key]
                ]
            ),
            nxt_available=None,
        )

        self.assertEqual("BETWEEN_SESSIONS", eligible["phase"])
        self.assertTrue(eligible["future_session_exists"])
        self.assertEqual("FINAL_SESSION_ENDED", non_nxt["phase"])
        self.assertFalse(non_nxt["future_session_exists"])
        self.assertIn("extra2", non_nxt["unavailable_sessions"])
        self.assertEqual("FINAL_SESSION_ENDED", unknown["phase"])

        with (
            patch.object(policy, "stock_nxt_availability", return_value=False),
            patch.object(ats_utils, "read_operation_policy", return_value=self.policy),
        ):
            decision = policy.auto_trade_setting_start_target_decision(
                self.window,
                state,
                "005930",
                config=config,
                now_dt=now,
            )
            category = policy.auto_trade_stock_operation_category(
                self.window,
                stock_code="005930",
                persisted_trade_started=False,
                operation_excluded=False,
                review_required=False,
                config=config,
                state=state,
                now_dt=now,
            )

        self.assertFalse(decision["allowed"])
        self.assertEqual("FINAL_SESSION_ENDED", decision["reason"])
        self.assertEqual("ended", category)

    def test_non_nxt_ats_cannot_gain_order_time_authority(self):
        config = {"operation_mode": "CONTINUOUS"}
        state = {
            "status": "RUNNING",
            "trade_enabled": True,
            "manual_ats_selection": {"selected_sessions": ["extra2"]},
        }
        ats_open = datetime(2026, 9, 19, 16, 0, 0)

        with (
            patch.object(ats_utils, "read_operation_policy", return_value=self.policy),
            patch.object(state_policy, "read_operation_policy", return_value=self.policy),
            patch.object(order_permission, "stock_nxt_availability", return_value=True),
        ):
            eligible = order_permission.canonical_stock_trading_time_status(
                config=config,
                state=state,
                now_dt=ats_open,
                stock_code="005930",
            )
        with (
            patch.object(ats_utils, "read_operation_policy", return_value=self.policy),
            patch.object(state_policy, "read_operation_policy", return_value=self.policy),
            patch.object(order_permission, "stock_nxt_availability", return_value=False),
        ):
            blocked = order_permission.canonical_stock_trading_time_status(
                config=config,
                state=state,
                now_dt=ats_open,
                stock_code="005930",
            )

        self.assertTrue(eligible["active"])
        self.assertEqual("ACTIVE_ATS", eligible["reason"])
        self.assertFalse(blocked["active"])
        self.assertEqual("OUTSIDE_OPERATION_TIME", blocked["reason"])

    def test_non_nxt_final_session_retirement_ignores_impossible_future_ats(self):
        config = {"operation_mode": "CONTINUOUS"}
        state = {
            "status": "RUNNING",
            "manual_ats_selection": {"selected_sessions": ["extra2"]},
        }
        now = datetime(2026, 9, 19, 15, 30, 0)

        with (
            patch.object(run_control, "read_operation_policy", return_value=self.policy),
            patch.object(ats_utils, "read_operation_policy", return_value=self.policy),
            patch.object(run_control, "stock_nxt_availability", return_value=False),
        ):
            phase = run_control.auto_trade_final_session_phase(
                config,
                state,
                stock_code="005930",
                now_dt=now,
            )

        self.assertEqual("FINAL_SESSION_ENDED", phase["phase"])
        self.assertTrue(phase["final_session_ended"])
        self.assertIn("extra2", phase["unavailable_sessions"])

    def test_waiting_category_exists_only_while_reentry_horizon_remains(self):
        scheduled = {
            "operation_mode": "SCHEDULED",
            "start_time": "09:00:00",
            "end_buy_time": "13:30:00",
        }
        state = {
            "status": "EARLY_CLOSED",
            "trade_enabled": True,
            "trade_started_at": "2026-09-19 09:00:00",
            "early_close_requested_at": "2026-09-19 10:00:00",
            "early_close_source": "OPERATOR",
            "operation_notice": "EARLY_CLOSE_COMPLETED",
        }
        local_policy = dict(self.policy)
        local_policy["scheduled_operation"] = {
            "default_start_time": "09:00:00",
            "default_end_buy_time": "13:30:00",
        }

        with (
            patch.object(ats_utils, "read_operation_policy", return_value=local_policy),
            patch.object(policy, "read_operation_policy", return_value=local_policy),
        ):
            waiting_category = policy.auto_trade_stock_operation_category(
                self.window,
                stock_code="005930",
                persisted_trade_started=True,
                operation_excluded=False,
                review_required=False,
                config=scheduled,
                state=state,
                now_dt=datetime(2026, 9, 19, 11, 0, 0),
            )
            waiting_projection = policy.auto_trade_setting_row_projection(
                state,
                scheduled,
                operation_category=waiting_category,
                holding_qty=0,
                current_session_trade_started=False,
                persisted_trade_started=True,
                now_dt=datetime(2026, 9, 19, 11, 0, 0),
            )
            ended_category = policy.auto_trade_stock_operation_category(
                self.window,
                stock_code="005930",
                persisted_trade_started=True,
                operation_excluded=False,
                review_required=False,
                config=scheduled,
                state=state,
                now_dt=datetime(2026, 9, 19, 13, 30, 0),
            )
            ended_projection = policy.auto_trade_setting_row_projection(
                state,
                scheduled,
                operation_category=ended_category,
                holding_qty=0,
                current_session_trade_started=False,
                persisted_trade_started=True,
                now_dt=datetime(2026, 9, 19, 13, 30, 0),
            )

        self.assertEqual("waiting", waiting_category)
        self.assertEqual("감시/대기", waiting_projection["display_status"])
        self.assertEqual("ended", ended_category)
        self.assertEqual("운영종료", ended_projection["display_status"])
        self.assertEqual("-", ended_projection["method_text"])

    def test_manual_ats_gap_is_waiting_but_last_effective_end_is_terminal(self):
        config = {"operation_mode": "CONTINUOUS"}
        state = {
            "status": "WAIT_BUY",
            "manual_ats_selection": {"selected_sessions": ["extra2"]},
        }
        with (
            patch.object(ats_utils, "read_operation_policy", return_value=self.policy),
            patch.object(policy, "read_operation_policy", return_value=self.policy),
        ):
            gap = policy.auto_trade_stock_operation_category(
                self.window,
                stock_code="005930",
                persisted_trade_started=False,
                operation_excluded=False,
                review_required=False,
                config=config,
                state=state,
                now_dt=datetime(2026, 9, 19, 15, 30, 0),
            )
            final = policy.auto_trade_stock_operation_category(
                self.window,
                stock_code="005930",
                persisted_trade_started=False,
                operation_excluded=False,
                review_required=False,
                config=config,
                state=state,
                now_dt=datetime(2026, 9, 19, 19, 50, 0),
            )

        self.assertEqual("waiting", gap)
        self.assertEqual("ended", final)

    def test_early_closed_waiting_stock_can_reenter_before_scheduled_close_boundary(self):
        config = {
            "operation_mode": "SCHEDULED",
            "start_time": "09:00:00",
            "end_buy_time": "13:30:00",
        }
        state = {
            "status": "EARLY_CLOSED",
            "trade_enabled": True,
            "trade_started_at": "2026-09-19 09:00:00",
            "early_close_requested_at": "2026-09-19 10:00:00",
            "early_close_source": "OPERATOR",
            "operation_notice": "EARLY_CLOSE_COMPLETED",
        }
        local_policy = dict(self.policy)
        local_policy["scheduled_operation"] = {
            "default_start_time": "09:00:00",
            "default_end_buy_time": "13:30:00",
        }

        with patch.object(
            ats_utils,
            "read_operation_policy",
            return_value=local_policy,
        ):
            allowed = policy.auto_trade_setting_start_target_decision(
                self.window,
                state,
                "005930",
                config=config,
                now_dt=datetime(2026, 9, 19, 11, 0, 0),
            )
            blocked = policy.auto_trade_setting_start_target_decision(
                self.window,
                state,
                "005930",
                config=config,
                now_dt=datetime(2026, 9, 19, 13, 30, 0),
            )

        self.assertTrue(allowed["allowed"])
        self.assertFalse(blocked["allowed"])
        self.assertEqual("FINAL_SESSION_ENDED", blocked["reason"])

    def test_completed_close_statuses_are_startable_when_row_is_waiting(self):
        config = {
            "operation_mode": "SCHEDULED",
            "start_time": "09:00:00",
            "end_buy_time": "13:30:00",
        }
        local_policy = dict(self.policy)
        local_policy["scheduled_operation"] = {
            "default_start_time": "09:00:00",
            "default_end_buy_time": "13:30:00",
        }

        with patch.object(
            ats_utils,
            "read_operation_policy",
            return_value=local_policy,
        ):
            for raw_status in ("EARLY_CLOSED", "AUTO_CLOSED", "LIQUIDATED"):
                with self.subTest(raw_status=raw_status):
                    decision = policy.auto_trade_setting_start_target_decision(
                        self.window,
                        {
                            "status": raw_status,
                            "trade_enabled": True,
                            "trade_started_at": "2026-09-19 09:00:00",
                        },
                        "005930",
                        config=config,
                        now_dt=datetime(2026, 9, 19, 11, 0, 0),
                    )
                    self.assertTrue(decision["allowed"])

    def test_order_permission_requires_program_and_actual_exchange_overlap(self):
        scheduled = {
            "operation_mode": "SCHEDULED",
            "start_time": "08:50:00",
            "end_buy_time": "13:30:00",
        }
        state = {"status": "RUNNING", "trade_enabled": True}
        with (
            patch.object(ats_utils, "read_operation_policy", return_value=self.policy),
            patch.object(state_policy, "read_operation_policy", return_value=self.policy),
        ):
            pre_open = order_permission.canonical_stock_trading_time_status(
                config=scheduled,
                state=state,
                now_dt=datetime(2026, 9, 19, 8, 55, 0),
            )
            regular_open = order_permission.canonical_stock_trading_time_status(
                config=scheduled,
                state=state,
                now_dt=datetime(2026, 9, 19, 9, 0, 0),
            )

        self.assertFalse(pre_open["active"])
        self.assertTrue(regular_open["active"])

        extended_policy = _operation_policy()
        extended_policy["regular_market"] = {
            "start_time": "09:00:00",
            "end_time": "15:40:00",
        }
        continuous = {"operation_mode": "CONTINUOUS"}
        with (
            patch.object(
                ats_utils,
                "read_operation_policy",
                return_value=extended_policy,
            ),
            patch.object(
                state_policy,
                "read_operation_policy",
                return_value=extended_policy,
            ),
        ):
            before_exchange_close = order_permission.canonical_stock_trading_time_status(
                config=continuous,
                state=state,
                now_dt=datetime(2026, 9, 19, 15, 25, 0),
            )
            after_exchange_close = order_permission.canonical_stock_trading_time_status(
                config=continuous,
                state=state,
                now_dt=datetime(2026, 9, 19, 15, 30, 0),
            )

        self.assertTrue(before_exchange_close["active"])
        self.assertFalse(after_exchange_close["active"])

    def test_configured_ats_can_start_waiting_in_gap_but_cannot_trade_before_exchange_open(self):
        config = {"operation_mode": "CONTINUOUS"}
        state = {
            "status": "WAIT_BUY",
            "manual_ats_selection": {
                "selected_sessions": ["extra2"],
            },
        }
        gap = datetime(2026, 9, 19, 15, 35, 0)
        ats_open = datetime(2026, 9, 19, 15, 40, 0)

        with (
            patch.object(ats_utils, "read_operation_policy", return_value=self.policy),
            patch.object(state_policy, "read_operation_policy", return_value=self.policy),
        ):
            start = policy.auto_trade_setting_start_target_decision(
                self.window,
                state,
                "005930",
                config=config,
                now_dt=gap,
            )
            gap_trade = order_permission.canonical_stock_trading_time_status(
                config=config,
                state=state,
                now_dt=gap,
            )
            ats_trade = order_permission.canonical_stock_trading_time_status(
                config=config,
                state=state,
                now_dt=ats_open,
            )

        self.assertTrue(start["allowed"])
        self.assertEqual("BETWEEN_SESSIONS", start["session_phase"]["phase"])
        self.assertFalse(gap_trade["active"])
        self.assertTrue(ats_trade["active"])
        self.assertEqual("ACTIVE_ATS", ats_trade["reason"])


    def test_selective_restart_after_today_normal_end_keeps_other_waiting_stocks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            targets = []
            for code in ("005930", "000660"):
                stock_dir = root / f"{code}_Test"
                stock_dir.mkdir()
                (stock_dir / "config.json").write_text(
                    json.dumps(
                        {
                            "assigned_routine_instance_id": "instance-a",
                            "routine_instance_name": "Routine A",
                            "operation_excluded": False,
                        }
                    ),
                    encoding="utf-8",
                )
                (stock_dir / "state.json").write_text(
                    json.dumps({"status": "WAIT_BUY", "trade_enabled": False}),
                    encoding="utf-8",
                )
                targets.append((stock_dir, code, "Test"))

            window = SimpleNamespace(
                running_registered_operation_targets=lambda: [],
                registered_operation_targets=lambda: list(targets),
            )
            with (
                patch.object(
                    run_control,
                    "read_operation_state",
                    return_value={
                        "operation_date": run_control.date.today().isoformat(),
                        "operation_status": "NORMAL_ENDED",
                        "operation_started_at": (
                            f"{run_control.date.today().isoformat()} 09:00:00"
                        ),
                    },
                ),
                patch.object(
                    run_control,
                    "auto_trade_start_selected_auto_trades",
                    return_value={"ok": True, "reason": "STARTED"},
                ) as start_backend,
                patch.object(run_control, "refresh_auto_trade_views"),
            ):
                result = run_control.auto_trade_start_selected_rows_auto_trades(
                    window,
                    selected_targets=[targets[0]],
                    source="auto_trade_context_menu",
                )

            self.assertTrue(result["ok"])
            start_backend.assert_called_once()
            self.assertFalse(
                json.loads(
                    (targets[1][0] / "config.json").read_text(encoding="utf-8")
                )["operation_excluded"]
            )



class _MockApi:
    def is_connected(self):
        return True


class MockWaitingReentryHostContractTest(unittest.TestCase):
    def test_non_nxt_reference_blocks_mock_future_ats_reentry(self):
        with tempfile.TemporaryDirectory() as temp:
            project_root = Path(temp) / "project"
            project_root.mkdir()
            policy_snapshot = {
                "regular_market": {
                    "start_time": "09:00:00",
                    "end_time": "15:20:00",
                },
                "scheduled_operation": {
                    "default_start_time": "09:00:00",
                    "default_end_buy_time": "13:30:00",
                },
                "manual_operation": {"use_regular_market": True},
                "extra_sessions": [
                    {
                        "enabled": True,
                        "start_time": "08:00:00",
                        "end_time": "08:50:00",
                    },
                    {
                        "enabled": True,
                        "start_time": "15:40:00",
                        "end_time": "19:50:00",
                    },
                ],
            }
            now = datetime(2026, 9, 19, 15, 30, 0)
            host = MockValidationHost(
                _MockApi(),
                project_root=project_root,
                now_factory=lambda: now,
                operation_policy_provider=lambda: policy_snapshot,
                candles_provider=lambda **_kwargs: {
                    "available": False,
                    "candles": [],
                    "availability_state": "SOURCE_UNAVAILABLE",
                    "available_minute_candles": 0,
                },
            )
            self.addCleanup(host.dispose)
            actions = MockValidationUIActions(host)
            reference = build_mock_reference_snapshot(
                stock={
                    "code": "005930",
                    "name": "Test",
                    "stock_path": "stocks/005930_Test",
                    "nxt_available": False,
                },
                routine_instances=[
                    {
                        "instance_id": "A",
                        "definition_id": "indicator_follow",
                        "routine_type": "INDICATOR_FOLLOW",
                        "display_name": "Routine A",
                        "group_id": "group-a",
                    }
                ],
                rules_by_instance_id={"A": {"version": 1}},
                created_at="2026-09-19T08:59:00+09:00",
            )
            created = actions.create_waiting_session(
                reference,
                effective_settings_by_instance={
                    "A": {
                        "initial_buy": {"mode": "QUANTITY", "value": 1},
                        "operation_schedule": {
                            "start_time": "09:00:00",
                            "end_buy_time": "13:30:00",
                        },
                        "operation_mode": "CONTINUOUS",
                        "manual_ats": {"selected_sessions": ["extra2"]},
                    }
                },
            )
            document = created["document"]
            admission = host._instance_start_admission(
                document,
                "A",
                now,
                operation_policy=policy_snapshot,
            )

            self.assertIs(document["reference_snapshot"]["nxt_available"], False)
            self.assertFalse(admission["allowed"])
            self.assertEqual("FINAL_SESSION_ENDED", admission["reason"])
            self.assertIn("extra2", admission["session_phase"]["unavailable_sessions"])

    def test_ended_operation_record_is_restartable_only_while_execution_is_waiting(self):
        with tempfile.TemporaryDirectory() as temp:
            project_root = Path(temp) / "project"
            project_root.mkdir()
            policy_snapshot = {
                "regular_market": {
                    "start_time": "09:00:00",
                    "end_time": "15:20:00",
                },
                "scheduled_operation": {
                    "default_start_time": "09:00:00",
                    "default_end_buy_time": "13:30:00",
                },
                "manual_operation": {"use_regular_market": True},
                "extra_sessions": [],
            }
            host = MockValidationHost(
                _MockApi(),
                project_root=project_root,
                now_factory=lambda: datetime(2026, 9, 19, 11, 0, 0),
                operation_policy_provider=lambda: policy_snapshot,
                candles_provider=lambda **_kwargs: {
                    "available": False,
                    "candles": [],
                    "availability_state": "SOURCE_UNAVAILABLE",
                    "available_minute_candles": 0,
                },
            )
            self.addCleanup(host.dispose)
            actions = MockValidationUIActions(host)
            reference = build_mock_reference_snapshot(
                stock={
                    "code": "005930",
                    "name": "Test",
                    "stock_path": "stocks/005930_Test",
                },
                routine_instances=[
                    {
                        "instance_id": "A",
                        "definition_id": "indicator_follow",
                        "routine_type": "INDICATOR_FOLLOW",
                        "display_name": "Routine A",
                        "group_id": "group-a",
                    }
                ],
                rules_by_instance_id={"A": {"version": 1}},
                created_at="2026-09-19T08:59:00+09:00",
            )
            created = actions.create_waiting_session(
                reference,
                effective_settings_by_instance={
                    "A": {
                        "initial_buy": {"mode": "QUANTITY", "value": 1},
                        "operation_schedule": {
                            "start_time": "09:00:00",
                            "end_buy_time": "13:30:00",
                        },
                        "operation_mode": "SCHEDULED",
                        "manual_ats": {"selected_sessions": []},
                    }
                },
            )
            session_id = created["document"]["session"]["validation_session_id"]

            def completed_early_close(document):
                root = document.setdefault("mock_operation_lifecycle", {})
                root.setdefault("instance_operations", {})["A"] = {
                    "state": "ENDED",
                    "close_source": "EARLY",
                    "close_method": "ROUTINE",
                }
                document["session"]["state"] = "WAITING"
                document["instance_execution"]["A"]["state"] = "WAITING"
                document["instance_execution"]["A"]["progression_allowed"] = False
                return document

            current = host.repository.mutate_session(
                session_id,
                completed_early_close,
            )["document"]
            self.assertTrue(host._instance_lifecycle_start_available(current, "A"))
            from mock_validation_ui_projection import mock_instance_projection
            waiting_projection = mock_instance_projection(
                current,
                "A",
                as_of=datetime(2026, 9, 19, 11, 0, 0),
            )
            self.assertEqual("감시/대기", waiting_projection["display_status"])

            active_admission = host._instance_start_admission(
                current,
                "A",
                datetime(2026, 9, 19, 11, 0, 0),
                operation_policy=policy_snapshot,
            )
            final_admission = host._instance_start_admission(
                current,
                "A",
                datetime(2026, 9, 19, 13, 30, 0),
                operation_policy=policy_snapshot,
            )
            self.assertTrue(active_admission["allowed"])
            self.assertFalse(final_admission["allowed"])
            self.assertEqual("FINAL_SESSION_ENDED", final_admission["reason"])

            def terminal_execution(document):
                document["instance_execution"]["A"]["state"] = "ENDED"
                return document

            terminal = host.repository.mutate_session(
                session_id,
                terminal_execution,
            )["document"]
            self.assertFalse(host._instance_lifecycle_start_available(terminal, "A"))
            terminal_projection = mock_instance_projection(
                terminal,
                "A",
                as_of=datetime(2026, 9, 19, 13, 30, 0),
            )
            self.assertEqual("운영종료", terminal_projection["display_status"])


class EarlyCloseParticipantRetirementTest(unittest.TestCase):
    def test_completed_early_close_immediately_becomes_nonparticipant_waiting(self):
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_Test"
            stock_dir.mkdir()
            state_path = stock_dir / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "status": "EARLY_CLOSING",
                        "operation_notice": "EARLY_CLOSE_ORDER_PROGRESS",
                    }
                ),
                encoding="utf-8",
            )
            owner = _ParticipantOwner(("005930",))
            window = _Window(owner, state_path)

            persisted = _persist_early_close_execution_result(
                window,
                stock_dir=stock_dir,
                code="005930",
                name="Test",
                result={
                    "runtime_status": "EARLY_CLOSED",
                    "stage": "completed",
                },
            )

            self.assertTrue(persisted)
            self.assertEqual((), owner.current_session_operation_participant_stock_codes())
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual("EARLY_CLOSED", saved["status"])
            self.assertEqual("EARLY_CLOSE_COMPLETED", saved["operation_notice"])

            owner.register_current_session_operation_participants(("005930",))
            persisted_again = _persist_early_close_execution_result(
                window,
                stock_dir=stock_dir,
                code="005930",
                name="Test",
                result={
                    "runtime_status": "EARLY_CLOSED",
                    "stage": "completed",
                },
            )
            self.assertTrue(persisted_again)
            self.assertEqual((), owner.current_session_operation_participant_stock_codes())


if __name__ == "__main__":
    unittest.main()
