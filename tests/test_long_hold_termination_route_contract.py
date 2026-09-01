# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import gui_auto_trade_run_control as run_control
from gui_auto_trade_policy import auto_trade_current_session_operation_participant_codes
from operation_close_completion_check_service import (
    SOURCE_ORDER_FILL_STATE_COMMIT,
    check_global_close_completion_after_durable_update,
)
from stock_long_hold_policy import (
    ROUTE_ATS_FINAL_NO_TERMINATION,
    ROUTE_CARRYOVER,
    ROUTE_CLOSE_INTENT,
    ROUTE_CONTINUOUS_NO_CLOSE,
    classify_termination_route,
    long_hold_excludes_holding_review,
)
from tests.participant_owner_fixture import participant_owner


TODAY = "2026-08-26"


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class TerminationRouteTruthTableTests(unittest.TestCase):
    def _allowed(
        self,
        state: dict[str, object],
        *,
        mode: str,
        pending_buy: object = 0,
        pending_sell: object = 0,
        safety_issue: bool = False,
    ) -> bool:
        return long_hold_excludes_holding_review(
            True,
            state,
            holding_qty=3,
            buy_pending_qty=pending_buy,
            sell_pending_qty=pending_sell,
            safety_issue=safety_issue,
            operation_mode=mode,
            final_session_ended=True,
        )

    def test_manual_close_residual_is_never_long_hold(self) -> None:
        state = {
            "status": "EARLY_CLOSED",
            "holding_qty": 3,
            "early_close_requested_at": f"{TODAY} 14:00:00",
            "early_close_method": "시장가",
        }
        route = classify_termination_route(
            state,
            operation_mode="CONTINUOUS",
            final_session_ended=True,
        )
        self.assertEqual(ROUTE_CLOSE_INTENT, route["route"])
        self.assertTrue(route["route_completed"])
        self.assertFalse(self._allowed(state, mode="CONTINUOUS"))

    def test_manual_no_close_holding_is_normal_only_when_enabled(self) -> None:
        state = {"status": "MONITORING", "holding_qty": 3}
        route = classify_termination_route(
            state,
            operation_mode="CONTINUOUS",
            final_session_ended=True,
        )
        self.assertEqual(ROUTE_CONTINUOUS_NO_CLOSE, route["route"])
        self.assertTrue(self._allowed(state, mode="CONTINUOUS"))
        self.assertFalse(
            long_hold_excludes_holding_review(
                False,
                state,
                holding_qty=3,
                buy_pending_qty=0,
                sell_pending_qty=0,
                safety_issue=False,
                operation_mode="CONTINUOUS",
                final_session_ended=True,
            )
        )

    def test_scheduled_carryover_and_direct_close_are_distinct(self) -> None:
        carryover = {
            "status": "AUTO_CLOSING",
            "holding_qty": 3,
            "auto_close_requested_at": f"{TODAY} 13:30:00",
            "auto_close_method": "이월",
        }
        market = dict(carryover, auto_close_method="시장가", status="AUTO_CLOSED")
        self.assertEqual(
            ROUTE_CARRYOVER,
            classify_termination_route(
                carryover,
                operation_mode="SCHEDULED",
                final_session_ended=True,
            )["route"],
        )
        self.assertTrue(self._allowed(carryover, mode="SCHEDULED"))
        self.assertFalse(self._allowed(market, mode="SCHEDULED"))

    def test_ats_setting_is_not_termination_execution(self) -> None:
        for method in ("ROUTINE", "MARKET", "CURRENT_PRICE"):
            with self.subTest(method=method):
                state = {
                    "status": "MONITORING",
                    "holding_qty": 3,
                    "manual_ats_selection": {
                        "selected_sessions": ["extra2"],
                        "execution_method": method,
                    },
                }
                route = classify_termination_route(
                    state,
                    operation_mode="CONTINUOUS",
                    final_session_ended=True,
                )
                self.assertEqual(ROUTE_ATS_FINAL_NO_TERMINATION, route["route"])
                self.assertFalse(route["actual_termination_executed"])
                self.assertTrue(self._allowed(state, mode="CONTINUOUS"))

    def test_actual_ats_market_or_current_termination_residual_is_review(self) -> None:
        for method in ("MARKET", "CURRENT_PRICE"):
            with self.subTest(method=method):
                state = {
                    "status": "MONITORING",
                    "holding_qty": 3,
                    "manual_ats_selection": {
                        "selected_sessions": ["extra2"],
                        "execution_method": "ROUTINE",
                    },
                    "manual_ats_liquidation_request": {
                        "command_id": "ATS-END-1",
                        "sell_method": method,
                        "status": "COMPLETED",
                    },
                }
                route = classify_termination_route(
                    state,
                    operation_mode="CONTINUOUS",
                    final_session_ended=True,
                )
                self.assertEqual(ROUTE_CLOSE_INTENT, route["route"])
                self.assertTrue(route["actual_termination_executed"])
                self.assertFalse(self._allowed(state, mode="CONTINUOUS"))

    def test_carryover_routes_require_long_hold_policy(self) -> None:
        states = (
            (
                "SCHEDULED",
                {
                    "status": "AUTO_CLOSING",
                    "holding_qty": 3,
                    "auto_close_requested_at": f"{TODAY} 13:30:00",
                    "auto_close_method": "이월",
                },
            ),
            (
                "CONTINUOUS",
                {
                    "status": "MONITORING",
                    "holding_qty": 3,
                    "manual_ats_selection": {
                        "selected_sessions": ["extra2"],
                        "execution_method": "ROUTINE",
                    },
                },
            ),
        )
        for mode, state in states:
            with self.subTest(mode=mode):
                self.assertFalse(
                    long_hold_excludes_holding_review(
                        False,
                        state,
                        holding_qty=3,
                        buy_pending_qty=0,
                        sell_pending_qty=0,
                        safety_issue=False,
                        operation_mode=mode,
                        final_session_ended=True,
                    )
                )

    def test_pending_and_integrity_issues_are_never_exempt(self) -> None:
        state = {"status": "MONITORING", "holding_qty": 3}
        self.assertFalse(self._allowed(state, mode="CONTINUOUS", pending_buy=1))
        self.assertFalse(self._allowed(state, mode="CONTINUOUS", pending_sell="?"))
        self.assertFalse(self._allowed(state, mode="CONTINUOUS", safety_issue=True))
        self.assertFalse(
            self._allowed(
                dict(state, reconciliation_status="MISMATCH"),
                mode="CONTINUOUS",
            )
        )


class ImmediateReviewAndRetirementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.stocks = self.root / "stocks"
        self.runtime.mkdir()
        self.stocks.mkdir()
        self.queue = self.runtime / "order_queue.json"
        self.executions = self.runtime / "order_executions.json"
        self.locks = self.runtime / "order_locks.json"
        _write(self.queue, {"version": 1, "revision": 0, "orders": []})
        _write(self.executions, {"version": 1, "executions": []})
        _write(self.locks, {"version": 1, "locks": []})

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _stock(
        self,
        code: str,
        *,
        config: dict[str, object],
        state: dict[str, object],
    ) -> Path:
        stock_dir = self.stocks / f"{code}_Stock"
        stock_dir.mkdir()
        _write(stock_dir / "config.json", config)
        _write(stock_dir / "state.json", state)
        _write(stock_dir / "orders.json", {"orders": []})
        return stock_dir

    def test_terminal_close_residual_is_written_to_review_immediately(self) -> None:
        stock_dir = self._stock(
            "012210",
            config={"operation_mode": "CONTINUOUS"},
            state={
                "status": "EARLY_CLOSED",
                "holding_qty": 3,
                "early_close_requested_at": f"{TODAY} 14:00:00",
                "early_close_method": "시장가",
                "review_required": False,
            },
        )
        _write(
            self.runtime / "operation_state.json",
            {
                "operation_date": TODAY,
                "operation_status": "CLOSING",
                "operation_participant_stock_codes": ["012210"],
            },
        )
        _write(self.runtime / "positions.json", {"positions": [{"code": "012210", "quantity": 3}]})
        _write(self.runtime / "broker_holdings.json", {"broker_holdings": [{"code": "012210", "quantity": 3}]})
        policy = self.root / "operation_policy.json"
        _write(policy, {"review_policy": {"long_term_holding_enabled": True}})

        result = check_global_close_completion_after_durable_update(
            source=SOURCE_ORDER_FILL_STATE_COMMIT,
            today=TODAY,
            operation_state_path=self.runtime / "operation_state.json",
            stocks_dir=self.stocks,
            order_queue_path=self.queue,
            positions_path=self.runtime / "positions.json",
            broker_holdings_path=self.runtime / "broker_holdings.json",
            operation_policy_path=policy,
        )
        saved = json.loads((stock_dir / "state.json").read_text(encoding="utf-8"))

        self.assertFalse(result["normal_ended_applied"])
        self.assertTrue(result["immediate_review_results"][0]["changed"])
        self.assertEqual("REVIEW_REQUIRED", saved["status"])
        self.assertTrue(saved["review_required"])
        self.assertIn("HOLDING_REMAINS", saved["review_detail"])

    def test_review_is_marked_before_participant_retirement(self) -> None:
        stock_dir = self._stock(
            "012210",
            config={
                "operation_mode": "SCHEDULED",
                "start_time": "09:00:00",
                "end_buy_time": "13:30:00",
            },
            state={
                "status": "AUTO_CLOSED",
                "trade_started_at": f"{TODAY} 09:00:00",
                "holding_qty": 3,
                "avg_price": 1000,
                "auto_close_requested_at": f"{TODAY} 13:30:00",
                "auto_close_method": "시장가",
                "review_required": False,
            },
        )
        window = SimpleNamespace(
            _main_monitoring_auto_trade_operation_host=participant_owner({"012210"})
        )

        def mark_review(**kwargs):
            self.assertEqual(
                ("012210",),
                auto_trade_current_session_operation_participant_codes(window),
            )
            return {"ok": True, "changed": True, "stock_code": kwargs["stock_code"]}

        with (
            patch.object(
                run_control,
                "auto_trade_registered_operation_targets",
                return_value=[(stock_dir, "012210", "Stock")],
            ),
            patch.object(
                run_control,
                "read_operation_state",
                return_value={
                    "operation_date": TODAY,
                    "operation_status": "RUNNING",
                    "operation_participant_stock_codes": ["012210"],
                },
            ),
            patch.object(
                run_control,
                "read_review_policy",
                return_value={"long_term_holding_enabled": True},
            ),
            patch.object(
                run_control,
                "mark_end_of_operation_review_required",
                side_effect=mark_review,
            ) as marker,
        ):
            result = run_control.auto_trade_retire_time_ended_current_session_participants(
                window,
                now_dt=datetime(2026, 8, 26, 13, 31),
                order_queue_path=self.queue,
                order_executions_path=self.executions,
                order_locks_path=self.locks,
            )

        marker.assert_called_once()
        self.assertEqual((), result["removed"])
        self.assertEqual(("012210",), result["remaining"])
        self.assertTrue(result["evaluations"][0]["review_marked"])

    def test_terminal_no_close_pending_order_is_immediate_review(self) -> None:
        stock_dir = self._stock(
            "012210",
            config={"operation_mode": "CONTINUOUS"},
            state={
                "status": "MONITORING",
                "trade_started_at": f"{TODAY} 09:00:00",
                "holding_qty": 3,
                "avg_price": 1000,
                "review_required": False,
            },
        )
        _write(
            self.queue,
            {
                "version": 1,
                "revision": 1,
                "orders": [
                    {
                        "id": "NORMAL-BUY-1",
                        "code": "012210",
                        "side": "BUY",
                        "status": "ORDER_QUEUED",
                        "remaining_quantity": 1,
                    }
                ],
            },
        )
        window = SimpleNamespace(
            _main_monitoring_auto_trade_operation_host=participant_owner({"012210"}),
        )
        with (
            patch.object(
                run_control,
                "auto_trade_registered_operation_targets",
                return_value=[(stock_dir, "012210", "Stock")],
            ),
            patch.object(
                run_control,
                "read_operation_state",
                return_value={
                    "operation_date": TODAY,
                    "operation_status": "RUNNING",
                    "operation_participant_stock_codes": ["012210"],
                },
            ),
            patch.object(
                run_control,
                "read_operation_policy",
                return_value={
                    "regular_market": {"start_time": "09:00:00", "end_time": "15:20:00"},
                    "manual_operation": {"use_regular_market": True},
                },
            ),
            patch.object(
                run_control,
                "read_review_policy",
                return_value={"long_term_holding_enabled": True},
            ),
            patch.object(
                run_control,
                "mark_end_of_operation_review_required",
                return_value={"ok": True, "changed": True},
            ) as marker,
        ):
            result = run_control.auto_trade_retire_time_ended_current_session_participants(
                window,
                now_dt=datetime(2026, 8, 26, 15, 21),
                order_queue_path=self.queue,
                order_executions_path=self.executions,
                order_locks_path=self.locks,
            )

        self.assertEqual((), result["removed"])
        self.assertIn(
            "END_OF_OPERATION_SAFETY_REVIEW_REQUIRED",
            result["evaluations"][0]["blockers"],
        )
        self.assertEqual("PENDING_ORDER", marker.call_args.kwargs["reason_code"])

    def test_scheduled_carryover_long_hold_retires_without_review(self) -> None:
        stock_dir = self._stock(
            "012210",
            config={
                "operation_mode": "SCHEDULED",
                "start_time": "09:00:00",
                "end_buy_time": "13:30:00",
            },
            state={
                "status": "AUTO_CLOSING",
                "trade_started_at": f"{TODAY} 09:00:00",
                "holding_qty": 3,
                "avg_price": 1000,
                "auto_close_requested_at": f"{TODAY} 13:30:00",
                "auto_close_method": "이월",
                "review_required": False,
            },
        )
        window = SimpleNamespace(
            _main_monitoring_auto_trade_operation_host=participant_owner({"012210"}),
        )
        with (
            patch.object(
                run_control,
                "auto_trade_registered_operation_targets",
                return_value=[(stock_dir, "012210", "Stock")],
            ),
            patch.object(
                run_control,
                "read_operation_state",
                return_value={
                    "operation_date": TODAY,
                    "operation_status": "RUNNING",
                    "operation_participant_stock_codes": ["012210"],
                },
            ),
            patch.object(
                run_control,
                "read_review_policy",
                return_value={"long_term_holding_enabled": True},
            ),
        ):
            result = run_control.auto_trade_retire_time_ended_current_session_participants(
                window,
                now_dt=datetime(2026, 8, 26, 13, 31),
                order_queue_path=self.queue,
                order_executions_path=self.executions,
                order_locks_path=self.locks,
            )

        self.assertEqual(("012210",), result["removed"])
        self.assertTrue(result["evaluations"][0]["long_hold_allowed"])

    def test_ats_final_routine_long_hold_retires_without_review(self) -> None:
        stock_dir = self._stock(
            "012210",
            config={"operation_mode": "CONTINUOUS"},
            state={
                "status": "MONITORING",
                "trade_started_at": f"{TODAY} 09:00:00",
                "holding_qty": 3,
                "avg_price": 1000,
                "manual_ats_selection": {
                    "selected_sessions": ["extra2"],
                    "execution_method": "ROUTINE",
                },
                "review_required": False,
            },
        )
        window = SimpleNamespace(
            _main_monitoring_auto_trade_operation_host=participant_owner({"012210"}),
        )

        def session(key: str) -> dict[str, object]:
            return {
                "enabled": key == "extra2",
                "start_time": "15:40:00",
                "end_time": "19:50:00",
            }

        with (
            patch.object(
                run_control,
                "auto_trade_registered_operation_targets",
                return_value=[(stock_dir, "012210", "Stock")],
            ),
            patch.object(
                run_control,
                "read_operation_state",
                return_value={
                    "operation_date": TODAY,
                    "operation_status": "RUNNING",
                    "operation_participant_stock_codes": ["012210"],
                },
            ),
            patch.object(
                run_control,
                "read_operation_policy",
                return_value={
                    "regular_market": {"start_time": "09:00:00", "end_time": "15:20:00"},
                    "manual_operation": {"use_regular_market": True},
                },
            ),
            patch.object(run_control, "manual_ats_session_definition", side_effect=session),
            patch.object(
                run_control,
                "read_review_policy",
                return_value={"long_term_holding_enabled": True},
            ),
        ):
            result = run_control.auto_trade_retire_time_ended_current_session_participants(
                window,
                now_dt=datetime(2026, 8, 26, 19, 51),
                order_queue_path=self.queue,
                order_executions_path=self.executions,
                order_locks_path=self.locks,
            )

        self.assertEqual(("012210",), result["removed"])
        self.assertEqual((), result["remaining"])
        self.assertTrue(result["evaluations"][0]["long_hold_allowed"])


if __name__ == "__main__":
    unittest.main()
