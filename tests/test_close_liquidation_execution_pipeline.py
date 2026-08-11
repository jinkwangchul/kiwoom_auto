# -*- coding: utf-8 -*-

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import gui_auto_trade_close as close
import gui_auto_trade_setting_window as gui
import operation_policy_gate
import order_queue
from gui_auto_trade_policy import (
    auto_trade_setting_display_status_for_current_session,
    auto_trade_setting_early_close_progress_text,
)
from close_liquidation_execution_pipeline import (
    build_close_liquidation_candidate_preview,
    commit_close_liquidation_candidate_preview,
    normalize_direct_liquidation_method,
)


class CloseLiquidationExecutionPipelineTest(unittest.TestCase):
    def test_broker_acceptance_marks_first_routine_close_sell_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            stock_dir = Path(tmp) / "stocks" / "005930_삼성전자"
            stock_dir.mkdir(parents=True)
            (stock_dir / "config.json").write_text(
                json.dumps(
                    {"assigned_routine_instance_id": "routine-instance-1"},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            state_path = stock_dir / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "status": "EARLY_CLOSE",
                        "trade_enabled": True,
                        "early_close_requested_at": "2026-08-10 10:00:00",
                        "early_close_method": "루틴",
                        "close_routine_final_sell_ordered": False,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            order = {
                "source": "routine_signals",
                "side": "SELL",
                "code": "005930",
                "routine_instance_id": "routine-instance-1",
            }

            with patch.object(gui, "all_registered_stock_dirs", return_value=[stock_dir]):
                send_call_only = gui._mark_close_routine_final_sell_from_broker_acceptance(
                    order,
                    {"event_type": ""},
                    {"event_type": ""},
                )
                accepted = gui._mark_close_routine_final_sell_from_broker_acceptance(
                    order,
                    {"event_type": "ORDER_ACCEPTED"},
                    {"event_type": "ORDER_ACCEPTED", "recorded": True},
                )

            self.assertFalse(send_call_only["attempted"])
            self.assertTrue(accepted["marked"], accepted)
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertTrue(saved["close_routine_final_sell_ordered"])
            self.assertEqual("kiwoom_chejan", saved["close_routine_final_sell_source"])

    def test_direct_liquidation_normalizer_accepts_legacy_aliases(self):
        self.assertEqual("MARKET", normalize_direct_liquidation_method("시장가즉시"))
        self.assertEqual(
            "CURRENT_PRICE",
            normalize_direct_liquidation_method("현재가즉시"),
        )

    @staticmethod
    def _stock(root: Path, name: str = "005930_Samsung") -> Path:
        stock = root / name
        stock.mkdir(parents=True)
        (stock / "config.json").write_text(
            json.dumps(
                {"assigned_routine_instance_id": "routine-instance-1"}
            ),
            encoding="utf-8",
        )
        (stock / "state.json").write_text(
            json.dumps(
                {
                    "status": "RUNNING",
                    "holding_qty": 3,
                    "trade_enabled": True,
                    "real_trade_enabled": True,
                    "trade_started_at": "2026-07-27 09:00:00",
                }
            ),
            encoding="utf-8",
        )
        return stock

    def test_market_and_current_price_build_existing_sell_candidate_shape(self):
        with tempfile.TemporaryDirectory() as temp:
            stock = self._stock(Path(temp))
            market = build_close_liquidation_candidate_preview(
                stock,
                "005930",
                "Samsung",
                "MARKET",
                command_id="command-1",
                requested_at="2026-07-27 13:30:00",
                routine_instance_id="routine-instance-1",
                reason="EARLY_CLOSE",
            )
            current = build_close_liquidation_candidate_preview(
                stock,
                "005930",
                "Samsung",
                "CURRENT_PRICE",
                command_id="command-2",
                requested_at="2026-07-27 13:31:00",
                routine_instance_id="routine-instance-1",
                reason="INDIVIDUAL_LIQUIDATION",
                latest_price_reader=lambda _code, _name: 72500,
            )

        self.assertTrue(market["ok"])
        self.assertEqual(market["order_candidate"]["side"], "SELL")
        self.assertEqual(market["order_candidate"]["hoga"], "MARKET")
        self.assertEqual(market["order_candidate"]["quantity"], 3)
        self.assertTrue(current["ok"])
        self.assertEqual(current["order_candidate"]["hoga"], "CURRENT_PRICE")
        self.assertEqual(current["order_candidate"]["price"], 72500)

    def test_commit_reuses_approval_candidate_and_policy_pipeline(self):
        preview = {
            "ok": True,
            "command_id": "command-1",
            "requested_at": "2026-07-27 13:30:00",
            "order_candidate": {
                "id": "CLOSE_LIQUIDATION_command-1",
                "status": "PENDING",
                "side": "SELL",
            },
        }
        appender = Mock(
            return_value={"ok": True, "orders_created": 1}
        )
        policy = Mock(
            return_value={"ok": True, "after_status": "EXECUTABLE"}
        )
        result = commit_close_liquidation_candidate_preview(
            preview,
            candidate_appender=appender,
            approval_evaluator=lambda _candidate: {
                "approval_status": "APPROVED",
                "approval_reason": "approved",
            },
            policy_applier=policy,
        )
        self.assertTrue(result["ok"])
        appended = appender.call_args.args[0][0]
        self.assertEqual(appended["status"], "APPROVED")
        self.assertFalse(appended["execution_enabled"])
        policy.assert_called_once_with("CLOSE_LIQUIDATION_command-1")

    def test_commit_creates_executable_record_in_canonical_queue_pipeline(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = root / "runtime"
            stocks = root / "stocks"
            runtime.mkdir()
            stock = self._stock(stocks)
            queue_path = runtime / "order_queue.json"
            queue_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "revision": 0,
                        "updated_at": "",
                        "orders": [],
                    }
                ),
                encoding="utf-8",
            )
            (runtime / "operation_state.json").write_text(
                json.dumps({}),
                encoding="utf-8",
            )
            preview = build_close_liquidation_candidate_preview(
                stock,
                "005930",
                "Samsung",
                "MARKET",
                command_id="command-real-queue",
                requested_at="2026-07-27 13:30:00",
                routine_instance_id="routine-instance-1",
                reason="INDIVIDUAL_LIQUIDATION",
            )
            with (
                patch.object(order_queue, "ORDER_QUEUE_PATH", queue_path),
                patch.object(operation_policy_gate, "ORDER_QUEUE_PATH", queue_path),
                patch.object(operation_policy_gate, "STOCKS_DIR", stocks),
                patch.object(
                    operation_policy_gate,
                    "OPERATION_STATE_PATH",
                    runtime / "operation_state.json",
                ),
            ):
                result = commit_close_liquidation_candidate_preview(
                    preview,
                    approval_evaluator=lambda _candidate: {
                        "approval_status": "APPROVED",
                        "approval_reason": "approved",
                    },
                )
            queue = json.loads(queue_path.read_text(encoding="utf-8"))

        self.assertTrue(result["ok"])
        self.assertEqual(len(queue["orders"]), 1)
        self.assertEqual(queue["orders"][0]["status"], "EXECUTABLE")
        self.assertEqual(queue["orders"][0]["code"], "005930")

    def test_cancel_request_defers_liquidation_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            stock = self._stock(Path(temp))
            window = Mock()
            window.queue_pending_order_cancellations_for_stock_automatically.return_value = {
                "ok": True,
                "cancel_requested": 1,
                "cancel_pending": 0,
            }
            with patch.object(
                close,
                "build_close_liquidation_candidate_preview",
            ) as builder:
                result = close._start_close_liquidation_execution(
                    window,
                    stock_dir=stock,
                    code="005930",
                    name="Samsung",
                    method="시장가",
                    command_id="command-1",
                    requested_at="2026-07-27 13:30:00",
                    routine_instance_id="routine-instance-1",
                    reason="EARLY_CLOSE",
                )
        self.assertTrue(result["ok"])
        self.assertEqual(result["stage"], "awaiting_cancel_confirmation")
        builder.assert_not_called()

    def test_no_pending_order_enters_existing_executable_pipeline_once(self):
        with tempfile.TemporaryDirectory() as temp:
            stock = self._stock(Path(temp))
            window = Mock()
            window.queue_pending_order_cancellations_for_stock_automatically.return_value = {
                "ok": True,
                "cancel_requested": 0,
                "cancel_pending": 0,
            }
            window.process_executable_order_for_auto_trade.return_value = {
                "processed": True,
                "stage": "send_order",
            }
            with (
                patch.object(
                    close,
                    "pending_order_side_quantities",
                    return_value=(0, 0),
                ),
                patch.object(
                    close,
                    "read_execution_queue_records",
                    return_value={"ok": True, "records": ()},
                ),
                patch.object(
                    close,
                    "build_close_liquidation_candidate_preview",
                    return_value={"ok": True},
                ) as builder,
                patch.object(
                    close,
                    "commit_close_liquidation_candidate_preview",
                    return_value={
                        "ok": True,
                        "stage": "executable",
                        "order_id": "CLOSE_LIQUIDATION_command-1",
                    },
                ),
            ):
                result = close._start_close_liquidation_execution(
                    window,
                    stock_dir=stock,
                    code="005930",
                    name="Samsung",
                    method="시장가",
                    command_id="command-1",
                    requested_at="2026-07-27 13:30:00",
                    routine_instance_id="routine-instance-1",
                    reason="INDIVIDUAL_LIQUIDATION",
                )
        self.assertTrue(result["ok"])
        builder.assert_called_once()
        window.process_executable_order_for_auto_trade.assert_called_once_with(
            "CLOSE_LIQUIDATION_command-1"
        )

    def test_legacy_aliases_enter_direct_candidate_pipeline(self):
        with tempfile.TemporaryDirectory() as temp:
            stock = self._stock(Path(temp))
            for method, expected in (
                ("시장가즉시", "MARKET"),
                ("현재가즉시", "CURRENT_PRICE"),
            ):
                with self.subTest(method=method):
                    window = Mock()
                    window.queue_pending_order_cancellations_for_stock_automatically.return_value = {
                        "ok": True,
                        "cancel_requested": 0,
                        "cancel_pending": 0,
                    }
                    window.process_executable_order_for_auto_trade.return_value = {
                        "processed": True,
                        "stage": "send_order",
                    }
                    with (
                        patch.object(
                            close,
                            "pending_order_side_quantities",
                            return_value=(0, 0),
                        ),
                        patch.object(
                            close,
                            "read_execution_queue_records",
                            return_value={"ok": True, "records": ()},
                        ),
                        patch.object(
                            close,
                            "build_close_liquidation_candidate_preview",
                            return_value={"ok": True},
                        ) as builder,
                        patch.object(
                            close,
                            "commit_close_liquidation_candidate_preview",
                            return_value={
                                "ok": True,
                                "stage": "executable",
                                "order_id": f"CLOSE_LIQUIDATION_{expected}",
                            },
                        ),
                    ):
                        result = close._start_close_liquidation_execution(
                            window,
                            stock_dir=stock,
                            code="005930",
                            name="Samsung",
                            method=method,
                            command_id=expected,
                            requested_at="2026-07-27 13:30:00",
                            routine_instance_id="routine-instance-1",
                            reason="EARLY_CLOSE",
                        )

                    self.assertTrue(result["ok"])
                    self.assertNotEqual("policy_runtime_only", result["stage"])
                    self.assertEqual(expected, builder.call_args.args[3])

    def test_pending_early_close_recovery_canonicalizes_legacy_alias(self):
        with tempfile.TemporaryDirectory() as temp:
            stock = self._stock(Path(temp))
            state_path = stock / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state.update(
                {
                    "status": "EARLY_CLOSE",
                    "early_close_requested_at": "2026-07-27 13:30:00",
                    "early_close_method": "시장가즉시",
                    "operation_command_id": "legacy-command",
                }
            )
            state_path.write_text(json.dumps(state), encoding="utf-8")
            with (
                patch(
                    "gui_auto_trade_runtime.all_registered_stock_dirs",
                    return_value=[stock],
                ),
                patch.object(close, "_production_recovery_gate", return_value=None),
                patch.object(
                    close,
                    "_start_close_liquidation_execution",
                    return_value={"ok": True, "stage": "executable"},
                ) as execute,
                patch.object(close, "_persist_early_close_execution_result", return_value=True),
                patch.object(
                    close,
                    "check_global_close_completion_after_durable_update",
                    return_value={"ok": True},
                ),
            ):
                result = close.auto_trade_continue_pending_close_liquidations(Mock())

        self.assertEqual(1, result["processed"])
        self.assertEqual("시장가", execute.call_args.kwargs["method"])

    def test_pending_close_continuation_filters_to_requested_routine_instance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = self._stock(root, "005930_Target")
            other = self._stock(root, "000660_Other")
            other_config = json.loads((other / "config.json").read_text(encoding="utf-8"))
            other_config["assigned_routine_instance_id"] = "routine-instance-2"
            (other / "config.json").write_text(
                json.dumps(other_config),
                encoding="utf-8",
            )
            for stock, command_id in ((target, "target-command"), (other, "other-command")):
                state = json.loads((stock / "state.json").read_text(encoding="utf-8"))
                state.update(
                    {
                        "status": "EARLY_CLOSE",
                        "early_close_requested_at": "2026-07-27 10:00:00",
                        "early_close_method": "시장가",
                        "operation_command_id": command_id,
                    }
                )
                (stock / "state.json").write_text(json.dumps(state), encoding="utf-8")

            with (
                patch(
                    "gui_auto_trade_runtime.all_registered_stock_dirs",
                    return_value=[target, other],
                ),
                patch.object(close, "_production_recovery_gate", return_value=None),
                patch.object(
                    close,
                    "_start_close_liquidation_execution",
                    return_value={"ok": True, "stage": "executable"},
                ) as execute,
                patch.object(close, "_persist_early_close_execution_result", return_value=True),
                patch.object(
                    close,
                    "check_global_close_completion_after_durable_update",
                    return_value={"ok": True},
                ),
            ):
                result = close.auto_trade_continue_pending_close_liquidations(
                    Mock(),
                    limit=None,
                    target_routine_instance_ids={"routine-instance-1"},
                )

        self.assertEqual(1, result["processed"])
        execute.assert_called_once()
        self.assertEqual("005930", execute.call_args.kwargs["code"])
        self.assertEqual("시장가", execute.call_args.kwargs["method"])

    def test_cancel_confirmation_must_precede_liquidation_queue_entry(self):
        with tempfile.TemporaryDirectory() as temp:
            stock = self._stock(Path(temp))
            window = Mock()
            window.queue_pending_order_cancellations_for_stock_automatically.side_effect = [
                {
                    "ok": True,
                    "cancel_requested": 1,
                    "cancel_pending": 0,
                },
                {
                    "ok": True,
                    "cancel_requested": 0,
                    "cancel_pending": 0,
                },
            ]
            window.process_executable_order_for_auto_trade.return_value = {
                "processed": True,
                "stage": "send_order",
            }
            with (
                patch.object(
                    close,
                    "pending_order_side_quantities",
                    return_value=(0, 0),
                ),
                patch.object(
                    close,
                    "read_execution_queue_records",
                    return_value={"ok": True, "records": ()},
                ),
                patch.object(
                    close,
                    "build_close_liquidation_candidate_preview",
                    return_value={"ok": True},
                ) as builder,
                patch.object(
                    close,
                    "commit_close_liquidation_candidate_preview",
                    return_value={
                        "ok": True,
                        "stage": "executable",
                        "order_id": "CLOSE_LIQUIDATION_command-1",
                    },
                ),
            ):
                awaiting = close._start_close_liquidation_execution(
                    window,
                    stock_dir=stock,
                    code="005930",
                    name="Samsung",
                    method="시장가",
                    command_id="command-1",
                    requested_at="2026-07-27 13:30:00",
                    routine_instance_id="routine-instance-1",
                    reason="INDIVIDUAL_LIQUIDATION",
                )
                self.assertEqual(
                    awaiting["stage"],
                    "awaiting_cancel_confirmation",
                )
                builder.assert_not_called()

                resumed = close._start_close_liquidation_execution(
                    window,
                    stock_dir=stock,
                    code="005930",
                    name="Samsung",
                    method="시장가",
                    command_id="command-1",
                    requested_at="2026-07-27 13:30:00",
                    routine_instance_id="routine-instance-1",
                    reason="INDIVIDUAL_LIQUIDATION",
                )

        self.assertTrue(resumed["ok"])
        builder.assert_called_once()
        window.process_executable_order_for_auto_trade.assert_called_once_with(
            "CLOSE_LIQUIDATION_command-1"
        )

    def test_routine_policy_does_not_call_cancel_or_direct_order_pipeline(self):
        with tempfile.TemporaryDirectory() as temp:
            stock = self._stock(Path(temp))
            window = Mock()
            result = close._start_close_liquidation_execution(
                window,
                stock_dir=stock,
                code="005930",
                name="Samsung",
                method="루틴마감",
                command_id="command-1",
                requested_at="2026-07-27 13:30:00",
                routine_instance_id="routine-instance-1",
                reason="EARLY_CLOSE",
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["stage"], "policy_runtime_only")
        window.queue_pending_order_cancellations_for_stock_automatically.assert_not_called()
        window.process_executable_order_for_auto_trade.assert_not_called()

    def test_existing_executable_order_resumes_existing_execution_pipeline(self):
        window = Mock()
        window.process_executable_order_for_auto_trade.return_value = {
            "processed": True,
            "stage": "send_order",
            "send_order_result": {
                "status": "SEND_CALL_ACCEPTED",
                "queue_result_recorded": True,
            },
        }
        result = close._resume_existing_close_order(
            window,
            {
                "id": "CLOSE_LIQUIDATION_command-1",
                "status": "EXECUTABLE",
            },
            holding_qty=3,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["runtime_status"], "EARLY_CLOSING")
        window.process_executable_order_for_auto_trade.assert_called_once_with(
            "CLOSE_LIQUIDATION_command-1"
        )

    def test_existing_order_queued_reuses_send_order_and_rejects_failed_call(self):
        window = Mock()
        window.send_order_for_order_queued_automatically.return_value = {
            "status": "SEND_CALL_REJECTED",
            "queue_result_recorded": True,
        }
        result = close._resume_existing_close_order(
            window,
            {
                "id": "ORDER_QUEUED_command-1",
                "status": "ORDER_QUEUED",
            },
            holding_qty=3,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["runtime_status"], "REVIEW_REQUIRED")
        window.send_order_for_order_queued_automatically.assert_called_once_with(
            "ORDER_QUEUED_command-1"
        )

    def test_zero_holding_finishes_existing_early_close_order(self):
        result = close._resume_existing_close_order(
            Mock(),
            {
                "id": "CLOSE_LIQUIDATION_command-1",
                "status": "FILLED",
            },
            holding_qty=0,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["stage"], "completed")
        self.assertEqual(result["runtime_status"], "EARLY_CLOSED")

    def test_early_close_execution_result_uses_existing_runtime_writer(self):
        with tempfile.TemporaryDirectory() as temp:
            stock = self._stock(Path(temp))
            window = Mock()
            window.update_stock_status.return_value = True
            result = close._persist_early_close_execution_result(
                window,
                stock_dir=stock,
                code="005930",
                name="Samsung",
                result={
                    "ok": True,
                    "stage": "send_order",
                    "runtime_status": "EARLY_CLOSING",
                },
            )
        self.assertTrue(result)
        args = window.update_stock_status.call_args.args
        self.assertEqual(args[3], "EARLY_CLOSING")
        self.assertEqual(
            args[4]["operation_notice"],
            "EARLY_CLOSE_ORDER_PROGRESS",
        )

    def test_early_close_progress_text_covers_operator_states(self):
        self.assertEqual(
            auto_trade_setting_early_close_progress_text(
                {"operation_notice": "EARLY_CLOSE_NO_TARGET"}
            ),
            "조건 미충족",
        )
        self.assertEqual(
            auto_trade_setting_early_close_progress_text(
                {
                    "status": "EARLY_CLOSE",
                    "early_close_requested_at": "2026-07-27 13:30:00",
                    "early_close_method": "시장가",
                }
            ),
            "실행 예정",
        )
        self.assertEqual(
            auto_trade_setting_early_close_progress_text(
                {
                    "status": "EARLY_CLOSING",
                    "early_close_requested_at": "2026-07-27 13:30:00",
                }
            ),
            "주문 진행",
        )
        self.assertEqual(
            auto_trade_setting_early_close_progress_text(
                {
                    "status": "EARLY_CLOSED",
                    "early_close_requested_at": "2026-07-27 13:30:00",
                }
            ),
            "완료",
        )
        self.assertEqual(
            auto_trade_setting_early_close_progress_text(
                {"operation_notice": "EARLY_CLOSE_EXECUTION_FAILED"}
            ),
            "실패",
        )

    def test_completed_early_close_remains_visible_until_runtime_cleanup(self):
        state = {
            "status": "EARLY_CLOSED",
            "trade_enabled": True,
            "holding_qty": 0,
            "early_close_requested_at": "2026-07-27 13:30:00",
            "early_close_method": "시장가",
            "operation_notice": "EARLY_CLOSE_COMPLETED",
        }
        display = auto_trade_setting_display_status_for_current_session(
            state,
            {"operation_mode": "CONTINUOUS"},
            holding_qty=0,
            current_session_trade_started=True,
            persisted_trade_started=True,
        )
        self.assertEqual(display, "조기마감")

    def test_existing_cancel_pipeline_targets_only_matching_stock_and_routine(self):
        source = {
            "id": "ORDER_SOURCE",
            "order_id": "ORDER_SOURCE",
            "status": "BROKER_ACCEPTED",
            "broker_order_no": "12345",
            "remaining_quantity": 2,
            "account_no": "12345678",
            "code": "005930",
            "side": "BUY",
            "routine": "routine-instance-1",
            "created_at": "2026-07-27 09:30:00",
            "order_action": "NEW",
        }
        other = {
            **source,
            "id": "ORDER_OTHER",
            "order_id": "ORDER_OTHER",
            "broker_order_no": "67890",
            "code": "000660",
        }
        window = Mock()
        window._queue_data_for_manual_order_action.return_value = (
            {},
            [source, other],
            [],
        )
        window._pending_cancel_duplicate_reason.return_value = ""
        window._build_manual_cancel_order_queued_preview.return_value = {
            "order_queued_record_preview": {"id": "ORDER_QUEUED_CANCEL"}
        }
        window.send_order_for_order_queued_automatically.return_value = {
            "queue_result_recorded": True
        }
        with (
            patch.object(
                gui.AutoTradeSettingWindow,
                "queue_file_snapshot",
                return_value={"revision": 1, "sha256": "same"},
            ),
            patch.object(
                gui,
                "commit_execution_queue_write",
                return_value={
                    "committed": True,
                    "post_write_verified": True,
                },
            ) as commit,
        ):
            result = (
                gui.AutoTradeSettingWindow
                .queue_pending_order_cancellations_for_stock_automatically(
                    window,
                    "005930",
                    "routine-instance-1",
                    trading_day="2026-07-27",
                    started_at="2026-07-27 09:00:00",
                )
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["cancel_requested"], 1)
        self.assertEqual(
            result["cancel_order_identities"],
            [
                {
                    "order_queued_id": "ORDER_SOURCE",
                    "order_id": "ORDER_SOURCE",
                    "broker_order_no": "12345",
                }
            ],
        )
        commit.assert_called_once()
        window.send_order_for_order_queued_automatically.assert_called_once()

    def test_cancel_pipeline_rejects_display_name_as_routine_identity(self):
        source = {
            "id": "ORDER_SOURCE",
            "status": "BROKER_ACCEPTED",
            "broker_order_no": "12345",
            "remaining_quantity": 2,
            "code": "005930",
            "side": "BUY",
            "routine": "지표추종매매",
            "created_at": "2026-07-27 09:30:00",
            "order_action": "NEW",
        }
        window = Mock()
        window._queue_data_for_manual_order_action.return_value = (
            {},
            [source],
            [],
        )
        result = (
            gui.AutoTradeSettingWindow
            .queue_pending_order_cancellations_for_stock_automatically(
                window,
                "005930",
                "routine-instance-1",
                trading_day="2026-07-27",
                started_at="2026-07-27 09:00:00",
            )
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["cancel_requested"], 0)
        self.assertIn(
            "matching stock pending order lacks the required routine instance identity",
            result["blocked_reasons"],
        )

    def test_cancel_pipeline_excludes_orders_before_operation_start(self):
        source = {
            "id": "ORDER_SOURCE",
            "status": "BROKER_ACCEPTED",
            "broker_order_no": "12345",
            "remaining_quantity": 2,
            "code": "005930",
            "side": "BUY",
            "routine": "routine-instance-1",
            "created_at": "2026-07-27 08:59:59",
            "order_action": "NEW",
        }
        window = Mock()
        window._queue_data_for_manual_order_action.return_value = (
            {},
            [source],
            [],
        )
        result = (
            gui.AutoTradeSettingWindow
            .queue_pending_order_cancellations_for_stock_automatically(
                window,
                "005930",
                "routine-instance-1",
                trading_day="2026-07-27",
                started_at="2026-07-27 09:00:00",
            )
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["cancel_requested"], 0)
        window._build_manual_cancel_order_queued_preview.assert_not_called()

    def test_cancel_pipeline_fails_closed_when_scope_identity_is_missing(self):
        source = {
            "id": "ORDER_SOURCE",
            "status": "BROKER_ACCEPTED",
            "broker_order_no": "12345",
            "remaining_quantity": 2,
            "code": "005930",
            "side": "BUY",
            "routine": "routine-instance-1",
            "created_at": "2026-07-27 09:30:00",
            "order_action": "NEW",
        }
        window = Mock()
        window._queue_data_for_manual_order_action.return_value = (
            {},
            [source],
            [],
        )
        result = (
            gui.AutoTradeSettingWindow
            .queue_pending_order_cancellations_for_stock_automatically(
                window,
                "005930",
                "routine-instance-1",
                trading_day="2026-07-27",
                started_at="",
            )
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["cancel_requested"], 0)
        self.assertIn(
            "matching stock pending order cannot be scoped without trade_started_at",
            result["blocked_reasons"],
        )

    def test_pending_command_resumer_ignores_other_stock(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = self._stock(root, "005930_Samsung")
            other = self._stock(root, "000660_SKHynix")
            target_state = json.loads(
                (target / "state.json").read_text(encoding="utf-8")
            )
            target_state["individual_liquidation_request"] = {
                "status": "REQUESTED",
                "method": "시장가",
                "command_id": "command-1",
                "requested_at": "2026-07-27 13:30:00",
            }
            (target / "state.json").write_text(
                json.dumps(target_state),
                encoding="utf-8",
            )
            with (
                patch(
                    "gui_auto_trade_runtime.all_registered_stock_dirs",
                    return_value=[target, other],
                ),
                patch.object(
                    close,
                    "_start_close_liquidation_execution",
                    return_value={"ok": True, "stage": "send_order"},
                ) as start,
            ):
                result = close.auto_trade_continue_pending_close_liquidations(
                    Mock(),
                )
        self.assertEqual(result["processed"], 1)
        start.assert_called_once()
        self.assertEqual(start.call_args.kwargs["code"], "005930")

    def test_routine_close_orders_follow_final_sell_marker_at_auto_execution_gate(self):
        window = Mock()
        base_state = {
            "status": "EARLY_CLOSE",
            "trade_enabled": True,
            "real_trade_enabled": True,
            "signal_probe_only": False,
            "review_required": False,
            "early_close_requested_at": "2026-08-10 10:00:00",
            "early_close_method": "루틴",
        }

        for side in ("BUY", "SELL"):
            window.auto_trade_runtime_state_for_order.return_value = {
                "found": True,
                "state": dict(base_state, close_routine_final_sell_ordered=False),
            }
            reasons = gui.AutoTradeSettingWindow.auto_trade_execution_block_reasons(
                window,
                {"side": side},
            )
            self.assertEqual(reasons, [], side)

            window.auto_trade_runtime_state_for_order.return_value = {
                "found": True,
                "state": dict(base_state, close_routine_final_sell_ordered=True),
            }
            blocked = gui.AutoTradeSettingWindow.auto_trade_execution_block_reasons(
                window,
                {"side": side},
            )
            self.assertEqual(len(blocked), 1, side)
            self.assertIn("추가 주문 차단", blocked[0])

        cancel_reasons = (
            gui.AutoTradeSettingWindow.auto_trade_execution_block_reasons(
                window,
                {
                    "side": "BUY",
                    "execution_request": {
                        "request_preview": {
                            "side": "BUY",
                            "order_action": "CANCEL",
                        }
                    },
                },
            )
        )
        self.assertEqual(cancel_reasons, [])

    def test_auto_close_routine_allows_buy_before_final_sell_at_auto_execution_gate(self):
        window = Mock()
        window.auto_trade_runtime_state_for_order.return_value = {
            "found": True,
            "state": {
                "status": "AUTO_CLOSE",
                "trade_enabled": True,
                "real_trade_enabled": True,
                "signal_probe_only": False,
                "review_required": False,
                "auto_close_requested_at": "2026-08-10 15:20:00",
                "auto_close_method": "루틴매도신호",
                "close_routine_final_sell_ordered": False,
            },
        }
        self.assertEqual(
            [],
            gui.AutoTradeSettingWindow.auto_trade_execution_block_reasons(
                window,
                {"side": "BUY"},
            ),
        )


if __name__ == "__main__":
    unittest.main()
