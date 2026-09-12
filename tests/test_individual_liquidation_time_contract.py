# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import gui_auto_trade_close as close
import gui_auto_trade_policy as policy
import gui_auto_trade_run_control as run_control
import operation_command_service
from manual_ats_runtime import PROGRAM_SESSION_ID
from close_liquidation_transition_service import (
    TransitionEvidence,
    decide_close_liquidation_transition,
)
from tests.participant_owner_fixture import attach_participant_owner


class IndividualLiquidationTimeContractTests(unittest.TestCase):
    NOW_DATE = (2026, 8, 16)

    def setUp(self) -> None:
        patcher = patch.object(
            policy,
            "auto_trade_setting_today_date_text",
            return_value="2026-08-16",
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _operation_policy() -> dict[str, object]:
        return {
            "regular_market": {"end_time": "15:20:00"},
            "liquidation": {
                "method": "이월",
                "minutes_before_regular_close": "5",
            },
        }

    @staticmethod
    def _stock(root: Path, *, method: str = "", minutes: str = "5") -> Path:
        stock = root / "stocks" / "005930_Samsung"
        stock.mkdir(parents=True)
        (stock / "config.json").write_text(
            json.dumps({"assigned_routine_instance_id": "routine-instance-1"}),
            encoding="utf-8",
        )
        state: dict[str, object] = {
            "status": "RUNNING",
            "holding_qty": 3,
            "trade_enabled": True,
            "trade_started_at": "2026-08-16 09:00:00",
            "operation_policy_snapshot": {
                "operation_identity": "2026-08-16 09:00:00",
                "operation_mode": "SCHEDULED",
                "regular_market": {"end_time": "15:20:00"},
                "liquidation": {
                    "method": "이월",
                    "minutes_before_regular_close": "5",
                },
            },
        }
        if method:
            state["individual_liquidation_request"] = {
                "status": "REQUESTED",
                "method": method,
                "minutes_before_regular_close": minutes,
                "command_id": "command-existing",
                "operation_sequence": 1,
                "requested_at": "2026-08-16 13:00:00",
                "operation_identity": "2026-08-16 09:00:00",
            }
            state["operation_sequence"] = 1
        (stock / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (stock / "orders.json").write_text(json.dumps({"orders": []}), encoding="utf-8")
        return stock

    @staticmethod
    def _window(stock: Path) -> Mock:
        window = Mock()
        window._persistent_feature_owner_ref = None
        window.parent.return_value = None
        window.kiwoom_api.is_connected.return_value = True
        window.selected_stock_infos.return_value = [(stock, "005930", "Samsung")]
        attach_participant_owner(window, {"005930"})
        window.capture_stock_table_view_state.return_value = ([str(stock)], 0)
        return window

    @staticmethod
    def _guard(**kwargs):
        evidence = TransitionEvidence(
            liquidation_time_window_entered=bool(
                kwargs.get("liquidation_time_window_entered")
            )
        )
        decision = decide_close_liquidation_transition(
            policy_domain=kwargs.get("policy_domain"),
            current_policy=kwargs.get("current_policy"),
            requested_policy=kwargs.get("requested_policy"),
            evidence=evidence,
        )
        return SimpleNamespace(
            allowed=decision.allowed,
            reason_code=decision.reason_code,
            evidence_status="COMPLETE",
        )

    def _apply(self, stock: Path, method: str, now_dt: datetime):
        window = self._window(stock)
        with (
            patch.object(close, "PROJECT_ROOT", stock.parent.parent),
            patch.object(policy, "read_operation_policy", side_effect=self._operation_policy),
            patch.object(close, "evaluate_production_transition", side_effect=self._guard),
            patch.object(close, "_start_close_liquidation_execution") as start,
            patch.object(close, "refresh_auto_trade_views"),
            patch.object(close, "append_stock_log"),
            patch.object(close, "show_toast"),
            patch.object(operation_command_service, "observe_liquidation_requested"),
        ):
            result = close.auto_trade_apply_selected_individual_liquidation_method(
                window,
                method,
                "5",
                show_error_dialog=False,
                now_dt=now_dt,
            )
        return result, start, window

    def test_setting_market_before_window_only_persists_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock = self._stock(Path(temp))
            result, start, window = self._apply(
                stock,
                "시장가",
                datetime(*self.NOW_DATE, 13, 0),
            )
            state = json.loads((stock / "state.json").read_text(encoding="utf-8"))

        self.assertTrue(result["ok"])
        self.assertEqual("시장가", state["individual_liquidation_request"]["method"])
        start.assert_not_called()
        window.statusBarMessage.assert_called_once_with(
            "개별청산 설정 완료: 5분/시장가 / 대상 1개"
        )

    def test_pre_operation_setting_is_reserved_after_hours_without_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock = self._stock(Path(temp))
            state_path = stock / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state.update(
                {
                    "status": "WAIT_BUY",
                    "holding_qty": 0,
                    "trade_enabled": False,
                    "trade_started_at": "",
                }
            )
            state.pop("operation_policy_snapshot", None)
            state_path.write_text(json.dumps(state), encoding="utf-8")
            window = self._window(stock)
            attach_participant_owner(window, set())
            with (
                patch.object(close, "PROJECT_ROOT", stock.parent.parent),
                patch.object(policy, "read_operation_policy", side_effect=self._operation_policy),
                patch.object(close, "evaluate_production_transition", side_effect=self._guard),
                patch.object(close, "_start_close_liquidation_execution") as start,
                patch.object(close, "refresh_auto_trade_views"),
                patch.object(close, "append_stock_log"),
                patch.object(close, "show_toast"),
                patch.object(operation_command_service, "observe_liquidation_requested"),
            ):
                result = close.auto_trade_apply_selected_individual_liquidation_method(
                    window,
                    "현재가",
                    "10",
                    show_error_dialog=False,
                    now_dt=datetime(*self.NOW_DATE, 21, 0),
                )
            saved = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertTrue(result["ok"])
        request = saved["individual_liquidation_request"]
        self.assertEqual("NEXT_OPERATION", request["reservation_scope"])
        self.assertEqual("", request["operation_identity"])
        self.assertEqual(PROGRAM_SESSION_ID, request["program_session_id"])
        self.assertEqual("현재가", request["method"])
        self.assertEqual("10", request["minutes_before_regular_close"])
        self.assertEqual(
            "10분/현재가",
            policy.auto_trade_setting_liquidation_text(
                {"operation_mode": "SCHEDULED"}, state=saved
            ),
        )
        start.assert_not_called()

    def test_pre_operation_market_and_carryover_reservations_keep_execution_zero(self) -> None:
        for method, minutes, expected_text in (
            ("시장가", "5", "5분/시장가"),
            ("이월", "30", "이월"),
        ):
            with self.subTest(method=method), tempfile.TemporaryDirectory() as temp:
                stock = self._stock(Path(temp))
                state_path = stock / "state.json"
                state = json.loads(state_path.read_text(encoding="utf-8"))
                state.update(
                    {
                        "status": "WAIT_BUY",
                        "holding_qty": 0,
                        "trade_enabled": False,
                        "trade_started_at": "",
                    }
                )
                state.pop("operation_policy_snapshot", None)
                state_path.write_text(json.dumps(state), encoding="utf-8")
                orders_before = (stock / "orders.json").read_bytes()
                window = self._window(stock)
                attach_participant_owner(window, set())
                with (
                    patch.object(close, "PROJECT_ROOT", stock.parent.parent),
                    patch.object(policy, "read_operation_policy", side_effect=self._operation_policy),
                    patch.object(close, "evaluate_production_transition", side_effect=self._guard),
                    patch.object(close, "_start_close_liquidation_execution") as start,
                    patch.object(close, "refresh_auto_trade_views"),
                    patch.object(close, "append_stock_log"),
                    patch.object(close, "show_toast"),
                    patch.object(operation_command_service, "observe_liquidation_requested"),
                ):
                    result = close.auto_trade_apply_selected_individual_liquidation_method(
                        window,
                        method,
                        minutes,
                        show_error_dialog=False,
                        now_dt=datetime(*self.NOW_DATE, 21, 0),
                    )
                saved = json.loads(state_path.read_text(encoding="utf-8"))

                self.assertTrue(result["ok"])
                request = saved["individual_liquidation_request"]
                self.assertEqual("NEXT_OPERATION", request["reservation_scope"])
                self.assertEqual("", request["operation_identity"])
                self.assertEqual(
                    "" if method == "이월" else minutes,
                    request["minutes_before_regular_close"],
                )
                self.assertEqual(
                    expected_text,
                    policy.auto_trade_setting_liquidation_text(
                        {"operation_mode": "SCHEDULED"}, state=saved
                    ),
                )
                self.assertEqual(orders_before, (stock / "orders.json").read_bytes())
                self.assertFalse((stock.parent.parent / "runtime" / "order_queue.json").exists())
                start.assert_not_called()

    def test_pre_operation_reservation_binds_once_and_does_not_block_start(self) -> None:
        state = {
            "individual_liquidation_request": {
                "status": "REQUESTED",
                "reservation_scope": "NEXT_OPERATION",
                "operation_identity": "",
                "requested_at": "2026-08-15 20:00:00",
                "program_session_id": PROGRAM_SESSION_ID,
                "method": "시장가",
                "minutes_before_regular_close": "5",
            }
        }
        self.assertFalse(
            run_control._active_close_or_liquidation(
                state, datetime(*self.NOW_DATE, 8, 0)
            )
        )
        bound = run_control._bind_pending_individual_liquidation_request(
            state, "2026-08-16 09:00:00"
        )
        self.assertIsNotNone(bound)
        self.assertEqual("CURRENT_OPERATION", bound["reservation_scope"])
        self.assertEqual("2026-08-16 09:00:00", bound["operation_identity"])
        self.assertEqual("2026-08-16 09:00:00", bound["bound_at"])
        active_state = {
            **state,
            "trade_started_at": "2026-08-16 09:00:00",
            "operation_policy_snapshot": {
                "operation_identity": "2026-08-16 09:00:00"
            },
            "individual_liquidation_request": bound,
        }
        self.assertEqual(
            "시장가",
            policy.individual_liquidation_policy_from_state(active_state)["method"],
        )

    def test_next_operation_reservation_is_process_local_and_expires_on_restart(self) -> None:
        global_policy = {
            "regular_market": {"end_time": "15:20:00"},
            "liquidation": {
                "method": "시장가",
                "minutes_before_regular_close": "5",
            },
        }
        for method, minutes in (("현재가", "10"), ("이월", "")):
            with self.subTest(method=method), tempfile.TemporaryDirectory() as temp:
                stock = Path(temp) / "stocks" / "005930_Samsung"
                stock.mkdir(parents=True)
                (stock / "config.json").write_text("{}", encoding="utf-8")
                state = {
                    "status": "WAIT_BUY",
                    "individual_liquidation_request": {
                        "status": "REQUESTED",
                        "reservation_scope": "NEXT_OPERATION",
                        "operation_identity": "",
                        "program_session_id": "process-A",
                        "requested_at": "2026-08-16 20:00:00",
                        "method": method,
                        "minutes_before_regular_close": minutes,
                    },
                }
                state_path = stock / "state.json"
                state_path.write_text(json.dumps(state), encoding="utf-8")

                self.assertEqual(
                    method,
                    policy.pending_individual_liquidation_policy_from_state(
                        state,
                        program_session_id="process-A",
                    )["method"],
                )
                self.assertEqual(
                    {},
                    policy.pending_individual_liquidation_policy_from_state(
                        state,
                        program_session_id="process-B",
                    ),
                )
                self.assertIsNone(
                    run_control._bind_pending_individual_liquidation_request(
                        state,
                        "2026-08-17 09:00:00",
                    )
                )

                result = (
                    run_control.expire_stale_next_operation_individual_liquidation_reservations(
                        program_session_id="process-B",
                        stock_dirs=(stock,),
                    )
                )
                saved = json.loads(state_path.read_text(encoding="utf-8"))

                self.assertEqual((str(stock.resolve()),), result["expired"])
                self.assertEqual((), result["errors"])
                self.assertNotIn("individual_liquidation_request", saved)
                with patch.object(
                    policy,
                    "read_operation_policy",
                    return_value=global_policy,
                ):
                    self.assertEqual(
                        "5분/시장가",
                        policy.auto_trade_setting_liquidation_text(
                            {"operation_mode": "SCHEDULED"},
                            state=saved,
                        ),
                    )

    def test_restart_expiration_preserves_matching_current_operation_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock = Path(temp) / "stocks" / "005930_Samsung"
            stock.mkdir(parents=True)
            (stock / "config.json").write_text("{}", encoding="utf-8")
            request = {
                "status": "REQUESTED",
                "reservation_scope": "CURRENT_OPERATION",
                "operation_identity": "operation-1",
                "program_session_id": "process-A",
                "bound_at": "2026-08-16 09:00:00",
                "method": "현재가",
                "minutes_before_regular_close": "10",
            }
            state = {
                "status": "RUNNING",
                "trade_started_at": "operation-1",
                "operation_policy_snapshot": {"operation_identity": "operation-1"},
                "individual_liquidation_request": request,
            }
            state_path = stock / "state.json"
            state_path.write_text(json.dumps(state), encoding="utf-8")

            result = (
                run_control.expire_stale_next_operation_individual_liquidation_reservations(
                    program_session_id="process-B",
                    stock_dirs=(stock,),
                )
            )
            saved = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual((), result["expired"])
        self.assertEqual(request, saved["individual_liquidation_request"])
        self.assertEqual(
            "현재가",
            policy.individual_liquidation_policy_from_state(saved)["method"],
        )
        mismatched = {
            **saved,
            "operation_policy_snapshot": {"operation_identity": "operation-2"},
            "trade_started_at": "operation-2",
        }
        self.assertEqual(
            {},
            policy.individual_liquidation_policy_from_state(mismatched),
        )

    def test_pre_operation_early_close_participant_gate_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock = self._stock(Path(temp))
            window = self._window(stock)
            attach_participant_owner(window, set())
            availability = close.inspect_close_liquidation_availability(
                window,
                stock,
                "005930",
                intent=close.EARLY_CLOSE_REQUEST,
                requested_method="시장가",
                now_dt=datetime(*self.NOW_DATE, 13, 0),
            )
        self.assertFalse(availability.allowed)
        self.assertEqual("NOT_CURRENT_PARTICIPANT", availability.reason_code)

    def test_early_routine_returns_to_frozen_scheduled_auto_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock = self._stock(Path(temp))
            state_path = stock / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state.update(
                {
                    "status": "EARLY_CLOSE",
                    "early_close_requested_at": "2026-08-16 10:00:00",
                    "early_close_method": "루틴",
                    "operation_policy_snapshot": {
                        "operation_identity": "2026-08-16 09:00:00",
                        "operation_mode": "SCHEDULED",
                        "operation_schedule": {"end_buy_time": "13:30:00"},
                    },
                }
            )
            state_path.write_text(json.dumps(state), encoding="utf-8")
            window = self._window(stock)

            def write_state(_stock, _code, _name, status, metadata, _reason):
                current = json.loads(state_path.read_text(encoding="utf-8"))
                current.update(metadata)
                current["status"] = status
                state_path.write_text(json.dumps(current), encoding="utf-8")
                return True

            window.update_stock_status.side_effect = write_state
            result = close.auto_trade_return_selected_early_close_to_auto(
                window,
                now_dt=datetime(*self.NOW_DATE, 11, 0),
            )
            after = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertTrue(result["ok"])
        self.assertEqual("RUNNING", after["status"])
        self.assertEqual("", after["early_close_requested_at"])
        self.assertEqual("", after["early_close_method"])

    def test_continuous_early_close_cannot_return_to_auto(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock = self._stock(Path(temp))
            state_path = stock / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state.update(
                {
                    "status": "EARLY_CLOSE",
                    "early_close_requested_at": "2026-08-16 10:00:00",
                    "early_close_method": "루틴",
                    "operation_policy_snapshot": {
                        "operation_identity": "2026-08-16 09:00:00",
                        "operation_mode": "CONTINUOUS",
                        "operation_schedule": {"end_buy_time": "13:30:00"},
                    },
                }
            )
            state_path.write_text(json.dumps(state), encoding="utf-8")
            window = self._window(stock)
            result = close.auto_trade_return_selected_early_close_to_auto(
                window,
                now_dt=datetime(*self.NOW_DATE, 11, 0),
            )

        self.assertFalse(result["ok"])
        window.update_stock_status.assert_not_called()

    def test_hard_early_auto_return_rechecks_sell_fills_after_cancel(self) -> None:
        for filled_qty in (0, 1, 3):
            with self.subTest(filled_qty=filled_qty), tempfile.TemporaryDirectory() as temp:
                stock = self._stock(Path(temp))
                state_path = stock / "state.json"
                state = json.loads(state_path.read_text(encoding="utf-8"))
                state.update(
                    {
                        "status": "EARLY_CLOSING",
                        "early_close_requested_at": "2026-08-16 10:00:00",
                        "early_close_method": "시장가",
                        "close_transition_pending": {
                            "method": "AUTO_TIMELINE",
                            "requested_at": "2026-08-16 10:01:00",
                        },
                    }
                )
                state_path.write_text(json.dumps(state), encoding="utf-8")
                (stock / "orders.json").write_text(
                    json.dumps(
                        {
                            "orders": [
                                {
                                    "side": "SELL",
                                    "status": "CANCELLED",
                                    "filled_qty": filled_qty,
                                    "created_at": "2026-08-16 10:00:01",
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
                window = self._window(stock)

                def write_state(_stock, _code, _name, status, metadata, _reason):
                    current = json.loads(state_path.read_text(encoding="utf-8"))
                    current.update(metadata)
                    current["status"] = status
                    state_path.write_text(json.dumps(current), encoding="utf-8")
                    return True

                window.update_stock_status.side_effect = write_state
                window.queue_pending_order_cancellations_for_stock_automatically.return_value = {
                    "ok": True,
                    "cancel_requested": 0,
                    "cancel_pending": 0,
                }
                result = close._continue_close_method_transition(
                    window,
                    stock_dir=stock,
                    code="005930",
                    name="Samsung",
                    state=state,
                    routine_instance_id="routine-instance-1",
                )
                after = json.loads(state_path.read_text(encoding="utf-8"))

            self.assertIsNone(after["close_transition_pending"])
            if filled_qty == 0:
                self.assertTrue(result["ok"])
                self.assertEqual("early_auto_timeline_restored", result["stage"])
                self.assertEqual("RUNNING", after["status"])
            else:
                self.assertFalse(result["ok"])
                self.assertEqual("early_auto_return_filled_blocked", result["stage"])
                self.assertEqual("EARLY_CLOSING", after["status"])

    def test_hard_early_auto_return_initial_admission_blocks_existing_sell_fill(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock = self._stock(Path(temp))
            state_path = stock / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state.update(
                {
                    "status": "EARLY_CLOSING",
                    "early_close_requested_at": "2026-08-16 10:00:00",
                    "early_close_method": "시장가",
                    "operation_policy_snapshot": {
                        "operation_identity": "2026-08-16 09:00:00",
                        "operation_mode": "SCHEDULED",
                        "operation_schedule": {"end_buy_time": "13:30:00"},
                    },
                }
            )
            state_path.write_text(json.dumps(state), encoding="utf-8")
            (stock / "orders.json").write_text(
                json.dumps(
                    {
                        "orders": [
                            {
                                "side": "SELL",
                                "status": "PARTIAL_FILLED",
                                "filled_qty": 1,
                                "created_at": "2026-08-16 10:00:01",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            window = self._window(stock)
            result = close.auto_trade_return_selected_early_close_to_auto(
                window,
                now_dt=datetime(*self.NOW_DATE, 11, 0),
            )

        self.assertFalse(result["ok"])
        self.assertEqual("EARLY_AUTO_RETURN_BLOCKED", result["reason"])
        window.update_stock_status.assert_not_called()

    def test_market_and_current_can_return_to_carryover_before_window(self) -> None:
        for initial in ("시장가", "현재가"):
            with self.subTest(initial=initial), tempfile.TemporaryDirectory() as temp:
                stock = self._stock(Path(temp), method=initial)
                result, start, _window = self._apply(
                    stock,
                    "이월",
                    datetime(*self.NOW_DATE, 13, 30),
                )
                state = json.loads((stock / "state.json").read_text(encoding="utf-8"))
                self.assertTrue(result["ok"])
                self.assertEqual("이월", state["individual_liquidation_request"]["method"])
                start.assert_not_called()

    def test_market_and_current_cannot_return_to_carryover_after_window_entry(self) -> None:
        for initial in ("시장가", "현재가"):
            with self.subTest(initial=initial), tempfile.TemporaryDirectory() as temp:
                stock = self._stock(Path(temp), method=initial)
                result, start, _window = self._apply(
                    stock,
                    "이월",
                    datetime(*self.NOW_DATE, 15, 15),
                )
                state = json.loads((stock / "state.json").read_text(encoding="utf-8"))
                self.assertFalse(result["ok"])
                self.assertEqual(
                    "청산설정변경이 불가능합니다.",
                    result["message"],
                )
                self.assertEqual(initial, state["individual_liquidation_request"]["method"])
                start.assert_not_called()

    def test_first_individual_policy_cannot_be_set_after_its_time_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock = self._stock(Path(temp))
            result, start, _window = self._apply(
                stock,
                "시장가",
                datetime(*self.NOW_DATE, 15, 15),
            )
            state = json.loads((stock / "state.json").read_text(encoding="utf-8"))

        self.assertFalse(result["ok"])
        self.assertNotIn("individual_liquidation_request", state)
        start.assert_not_called()

    def test_new_minutes_cannot_retroactively_enter_an_earlier_window(self) -> None:
        state = {
            "individual_liquidation_request": {
                "status": "REQUESTED",
                "method": "시장가",
                "minutes_before_regular_close": "5",
                "requested_at": "2026-08-16 13:00:00",
            }
        }
        with patch.object(policy, "read_operation_policy", side_effect=self._operation_policy):
            self.assertTrue(
                policy.auto_trade_setting_individual_liquidation_window_entered(
                    state,
                    datetime(*self.NOW_DATE, 15, 12),
                    candidate_minutes_before_regular_close="10",
                )
            )

    def test_time_source_uses_regular_end_minus_configured_minutes(self) -> None:
        state = {
            "operation_policy_snapshot": {
                "operation_identity": "2026-08-16 09:00:00",
            },
            "individual_liquidation_request": {
                "status": "REQUESTED",
                "operation_identity": "2026-08-16 09:00:00",
                "method": "시장가",
                "minutes_before_regular_close": "5",
                "requested_at": "2026-08-16 13:00:00",
            }
        }
        with patch.object(policy, "read_operation_policy", side_effect=self._operation_policy):
            self.assertFalse(
                policy.auto_trade_setting_individual_liquidation_window_entered(
                    state,
                    datetime(*self.NOW_DATE, 15, 14, 59),
                )
            )
            self.assertTrue(
                policy.auto_trade_setting_individual_liquidation_window_entered(
                    state,
                    datetime(*self.NOW_DATE, 15, 15, 0),
                )
            )

    def test_newer_individual_policy_overrides_earlier_close_carryover_at_time_gate(self) -> None:
        state = {
            "status": "AUTO_CLOSING",
            "auto_close_method": "이월",
            "operation_policy_snapshot": {
                "operation_identity": "2026-08-16 09:00:00",
            },
            "individual_liquidation_request": {
                "status": "REQUESTED",
                "operation_identity": "2026-08-16 09:00:00",
                "method": "시장가",
                "minutes_before_regular_close": "5",
                "requested_at": "2026-08-16 13:00:00",
            },
        }
        with patch.object(policy, "read_operation_policy", side_effect=self._operation_policy):
            self.assertTrue(
                policy.auto_trade_setting_liquidation_active(
                    {},
                    3,
                    now_dt=datetime(*self.NOW_DATE, 15, 15),
                    display_status="자동마감",
                    state=state,
                )
            )

    def test_close_carryover_displays_no_liquidation_until_individual_override(self) -> None:
        base_state = {
            "status": "AUTO_CLOSING",
            "auto_close_method": "이월",
            "operation_policy_snapshot": {
                "operation_identity": "2026-08-16 09:00:00",
            },
        }
        with patch.object(policy, "read_operation_policy", side_effect=self._operation_policy):
            self.assertEqual(
                "-",
                policy.auto_trade_setting_liquidation_text(
                    {"operation_mode": "SCHEDULED"},
                    "자동마감",
                    base_state,
                ),
            )
            self.assertEqual(
                "5분/시장가",
                policy.auto_trade_setting_liquidation_text(
                    {"operation_mode": "SCHEDULED"},
                    "자동마감",
                    {
                        **base_state,
                        "individual_liquidation_request": {
                            "status": "REQUESTED",
                            "operation_identity": "2026-08-16 09:00:00",
                            "method": "시장가",
                            "minutes_before_regular_close": "5",
                            "requested_at": "2026-08-16 13:00:00",
                        },
                    },
                ),
            )

    def test_timer_enters_existing_pipeline_only_at_market_or_current_window(self) -> None:
        for method in ("시장가", "현재가"):
            with self.subTest(method=method), tempfile.TemporaryDirectory() as temp:
                stock = self._stock(Path(temp), method=method)
                with (
                    patch(
                        "gui_auto_trade_runtime.all_registered_stock_dirs",
                        return_value=[stock],
                    ),
                    patch.object(policy, "read_operation_policy", side_effect=self._operation_policy),
                    patch.object(
                        close,
                        "_start_close_liquidation_execution",
                        return_value={"ok": True, "stage": "send_order"},
                    ) as start,
                ):
                    before = close.auto_trade_continue_pending_close_liquidations(
                        Mock(),
                        now_dt=datetime(*self.NOW_DATE, 13, 0),
                    )
                    at_window = close.auto_trade_continue_pending_close_liquidations(
                        Mock(),
                        now_dt=datetime(*self.NOW_DATE, 15, 15),
                    )

                self.assertEqual(0, before["processed"])
                self.assertEqual(1, at_window["processed"])
                start.assert_called_once()

    def test_boundary_with_zero_holding_expires_setting_without_liquidation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock = self._stock(Path(temp), method="시장가")
            state_path = stock / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["holding_qty"] = 0
            state_path.write_text(json.dumps(state), encoding="utf-8")
            window = Mock()

            def persist(_stock, _code, _name, status, metadata, _reason):
                current = json.loads(state_path.read_text(encoding="utf-8"))
                current.update(metadata)
                current["status"] = status
                state_path.write_text(json.dumps(current), encoding="utf-8")
                return True

            window.update_stock_status.side_effect = persist
            with (
                patch(
                    "gui_auto_trade_runtime.all_registered_stock_dirs",
                    return_value=[stock],
                ),
                patch.object(policy, "read_operation_policy", side_effect=self._operation_policy),
                patch.object(close, "_start_close_liquidation_execution") as start,
            ):
                result = close.auto_trade_continue_pending_close_liquidations(
                    window,
                    now_dt=datetime(*self.NOW_DATE, 15, 15),
                )
            saved = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(1, result["processed"])
        self.assertEqual("completed_no_holding", result["results"][0]["stage"])
        self.assertEqual("COMPLETED", saved["individual_liquidation_request"]["status"])
        self.assertEqual(
            "NO_HOLDING_AT_BOUNDARY",
            saved["individual_liquidation_request"]["completion_reason"],
        )
        self.assertNotIn("liquidation_execution", saved)
        self.assertIsNone(
            run_control._bind_pending_individual_liquidation_request(
                saved, "2026-08-17 09:00:00"
            )
        )
        start.assert_not_called()

    def test_final_carryover_enters_cancel_reconcile_pipeline_at_cleanup_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock = self._stock(Path(temp), method="이월")
            with (
                patch(
                    "gui_auto_trade_runtime.all_registered_stock_dirs",
                    return_value=[stock],
                ),
                patch.object(policy, "read_operation_policy", side_effect=self._operation_policy),
                patch.object(
                    close,
                    "_start_close_liquidation_execution",
                    return_value={
                        "ok": True,
                        "stage": "carryover_completed",
                        "runtime_status": "LIQUIDATED",
                    },
                ) as start,
                patch.object(
                    close,
                    "check_global_close_completion_after_durable_update",
                    return_value={"checked": True},
                ),
            ):
                result = close.auto_trade_continue_pending_close_liquidations(
                    Mock(),
                    now_dt=datetime(*self.NOW_DATE, 15, 19, 30),
                )

        self.assertEqual(1, result["processed"])
        start.assert_called_once()

    def test_scheduled_operation_snapshot_admits_global_liquidation_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock = self._stock(Path(temp))
            state = json.loads((stock / "state.json").read_text(encoding="utf-8"))
            state["operation_policy_snapshot"] = {
                "operation_identity": state["trade_started_at"],
                "operation_mode": "SCHEDULED",
                "regular_market": {"end_time": "15:20:00"},
                "liquidation": {
                    "minutes_before_regular_close": "5",
                    "method": "시장가",
                },
            }
            (stock / "state.json").write_text(json.dumps(state), encoding="utf-8")
            with (
                patch(
                    "gui_auto_trade_runtime.all_registered_stock_dirs",
                    return_value=[stock],
                ),
                patch.object(
                    close,
                    "_start_close_liquidation_execution",
                    return_value={
                        "ok": True,
                        "stage": "completed",
                        "runtime_status": "LIQUIDATED",
                    },
                ) as start,
                patch.object(
                    close,
                    "check_global_close_completion_after_durable_update",
                    return_value={"checked": True},
                ),
            ):
                result = close.auto_trade_continue_pending_close_liquidations(
                    Mock(),
                    now_dt=datetime(*self.NOW_DATE, 15, 15),
                )
        self.assertEqual(1, result["processed"])
        self.assertEqual("시장가", start.call_args.kwargs["method"])

    def test_continuous_operation_without_close_does_not_enter_global_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock = self._stock(Path(temp))
            state = json.loads((stock / "state.json").read_text(encoding="utf-8"))
            state["operation_policy_snapshot"] = {
                "operation_identity": state["trade_started_at"],
                "operation_mode": "CONTINUOUS",
                "regular_market": {"end_time": "15:20:00"},
                "liquidation": {
                    "minutes_before_regular_close": "5",
                    "method": "시장가",
                },
            }
            (stock / "state.json").write_text(json.dumps(state), encoding="utf-8")
            with (
                patch(
                    "gui_auto_trade_runtime.all_registered_stock_dirs",
                    return_value=[stock],
                ),
                patch.object(close, "_start_close_liquidation_execution") as start,
            ):
                result = close.auto_trade_continue_pending_close_liquidations(
                    Mock(),
                    now_dt=datetime(*self.NOW_DATE, 15, 15),
                )
        self.assertEqual(0, result["processed"])
        start.assert_not_called()

    def test_completed_close_carryover_does_not_enter_scheduled_liquidation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock = self._stock(Path(temp))
            state = json.loads((stock / "state.json").read_text(encoding="utf-8"))
            state.update(
                {
                    "status": "EARLY_CLOSED",
                    "early_close_requested_at": "2026-08-16 10:00:00",
                    "early_close_method": "이월",
                    "termination_provenance": "CLOSE_CARRYOVER",
                    "operation_policy_snapshot": {
                        "operation_identity": state["trade_started_at"],
                        "operation_mode": "SCHEDULED",
                        "regular_market": {"end_time": "15:20:00"},
                        "liquidation": {
                            "minutes_before_regular_close": "5",
                            "method": "시장가",
                        },
                    },
                }
            )
            (stock / "state.json").write_text(json.dumps(state), encoding="utf-8")
            with (
                patch(
                    "gui_auto_trade_runtime.all_registered_stock_dirs",
                    return_value=[stock],
                ),
                patch.object(close, "_start_close_liquidation_execution") as start,
            ):
                result = close.auto_trade_continue_pending_close_liquidations(
                    Mock(),
                    now_dt=datetime(*self.NOW_DATE, 15, 15),
                )
        self.assertEqual(0, result["processed"])
        start.assert_not_called()

    def test_active_snapshot_is_not_hot_swapped_by_mutable_global_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock = self._stock(Path(temp))
            state = json.loads((stock / "state.json").read_text(encoding="utf-8"))
            state["operation_policy_snapshot"] = {
                "operation_identity": state["trade_started_at"],
                "operation_mode": "SCHEDULED",
                "regular_market": {"end_time": "15:20:00"},
                "liquidation": {
                    "minutes_before_regular_close": "10",
                    "method": "현재가",
                },
            }
            (stock / "state.json").write_text(json.dumps(state), encoding="utf-8")
            with (
                patch(
                    "gui_auto_trade_runtime.all_registered_stock_dirs",
                    return_value=[stock],
                ),
                patch.object(
                    policy,
                    "read_operation_policy",
                    return_value={
                        "regular_market": {"end_time": "16:00:00"},
                        "liquidation": {
                            "minutes_before_regular_close": "1",
                            "method": "이월",
                        },
                    },
                ),
                patch.object(
                    close,
                    "_start_close_liquidation_execution",
                    return_value={
                        "ok": True,
                        "stage": "completed",
                        "runtime_status": "LIQUIDATED",
                    },
                ) as start,
                patch.object(
                    close,
                    "check_global_close_completion_after_durable_update",
                    return_value={"checked": True},
                ),
            ):
                close.auto_trade_continue_pending_close_liquidations(
                    Mock(),
                    now_dt=datetime(*self.NOW_DATE, 15, 10),
                )
        self.assertEqual("현재가", start.call_args.kwargs["method"])

    def test_identity_less_old_override_is_not_reused_by_new_operation(self) -> None:
        state = {
            "trade_started_at": "2026-08-16 11:00:00",
            "operation_policy_snapshot": {
                "operation_identity": "2026-08-16 11:00:00",
                "operation_mode": "SCHEDULED",
                "regular_market": {"end_time": "15:20:00"},
                "liquidation": {
                    "minutes_before_regular_close": "5",
                    "method": "시장가",
                },
            },
            "individual_liquidation_request": {
                "status": "REQUESTED",
                "requested_at": "2026-08-16 09:30:00",
                "method": "현재가",
                "minutes_before_regular_close": "15",
            },
        }
        resolved, is_individual = policy.effective_liquidation_policy_for_config(
            {}, state
        )
        self.assertFalse(is_individual)
        self.assertEqual("시장가", resolved["method"])
        self.assertEqual("5", resolved["minutes_before_regular_close"])

    def test_identity_less_old_override_provenance_is_not_reused_at_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock = self._stock(Path(temp))
            state_path = stock / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["individual_liquidation_request"] = {
                "status": "REQUESTED",
                "requested_at": "2026-08-16 08:30:00",
                "command_id": "OLD-OPERATION-COMMAND",
                "method": "현재가",
                "minutes_before_regular_close": "15",
            }
            state_path.write_text(json.dumps(state), encoding="utf-8")
            with (
                patch(
                    "gui_auto_trade_runtime.all_registered_stock_dirs",
                    return_value=[stock],
                ),
                patch.object(
                    close,
                    "_start_close_liquidation_execution",
                    return_value={
                        "ok": True,
                        "stage": "carryover_completed",
                        "runtime_status": "LIQUIDATED",
                    },
                ) as start,
                patch.object(
                    close,
                    "check_global_close_completion_after_durable_update",
                    return_value={"checked": True},
                ),
            ):
                close.auto_trade_continue_pending_close_liquidations(
                    Mock(),
                    now_dt=datetime(*self.NOW_DATE, 15, 19, 30),
                )

        self.assertEqual("이월", start.call_args.kwargs["method"])
        self.assertNotEqual(
            "OLD-OPERATION-COMMAND", start.call_args.kwargs["command_id"]
        )
        self.assertNotEqual(
            "2026-08-16 08:30:00", start.call_args.kwargs["requested_at"]
        )

    def test_matching_operation_identity_keeps_individual_override(self) -> None:
        state = {
            "trade_started_at": "2026-08-16 11:00:00",
            "operation_policy_snapshot": {
                "operation_identity": "2026-08-16 11:00:00",
                "operation_mode": "SCHEDULED",
                "regular_market": {"end_time": "15:20:00"},
                "liquidation": {
                    "minutes_before_regular_close": "5",
                    "method": "시장가",
                },
            },
            "individual_liquidation_request": {
                "status": "REQUESTED",
                "requested_at": "2026-08-16 11:01:00",
                "operation_identity": "2026-08-16 11:00:00",
                "method": "현재가",
                "minutes_before_regular_close": "15",
            },
        }
        resolved, is_individual = policy.effective_liquidation_policy_for_config(
            {}, state
        )
        self.assertTrue(is_individual)
        self.assertEqual("현재가", resolved["method"])

    def test_active_operation_without_snapshot_never_starts_liquidation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock = self._stock(Path(temp))
            state = json.loads((stock / "state.json").read_text(encoding="utf-8"))
            state.pop("operation_policy_snapshot")
            state["early_close_requested_at"] = "2026-08-16 10:00:00"
            state["early_close_method"] = "시장가"
            (stock / "state.json").write_text(json.dumps(state), encoding="utf-8")
            with (
                patch(
                    "gui_auto_trade_runtime.all_registered_stock_dirs",
                    return_value=[stock],
                ),
                patch.object(close, "_start_close_liquidation_execution") as start,
            ):
                result = close.auto_trade_continue_pending_close_liquidations(
                    Mock(),
                    now_dt=datetime(*self.NOW_DATE, 15, 15),
                )

        self.assertEqual(0, result["processed"])
        self.assertEqual(1, result["blocked"])
        self.assertEqual(
            ["ACTIVE_OPERATION_POLICY_SNAPSHOT_MISSING"],
            result["results"][0]["blocked_reasons"],
        )
        start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
