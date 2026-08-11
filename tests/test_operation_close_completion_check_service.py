# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import broker_holding_recorder
import gui_auto_trade_close
import operation_policy_gate
import operator_reconciliation_service
import order_fill_state_service
from operation_close_completion_evaluator import (
    STATUS_CARRYOVER_DONE,
    STATUS_CLOSE_NOT_STARTED,
    STATUS_DONE,
    STATUS_EVIDENCE_CONFLICT,
    STATUS_HOLDING_REMAINS,
    STATUS_PENDING_ORDER,
    STATUS_REVIEW_REQUIRED,
    STATUS_UNKNOWN,
    today_text,
)
from operation_close_completion_check_service import (
    SOURCE_BROKER_HOLDING_COMMIT,
    SOURCE_EARLY_CLOSE_DURABLE_UPDATE,
    SOURCE_ORDER_FILL_STATE_COMMIT,
    SOURCE_STARTUP_RECOVERY,
    check_global_close_completion_after_durable_update,
)


class OperationCloseCompletionCheckServiceTests(unittest.TestCase):
    def _write_json(self, path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def _hashes(self, root: Path) -> dict[str, str]:
        return {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*.json"))
        }

    def _runtime_root(
        self,
        root: Path,
        *,
        operation_date: str = "2026-07-30",
    ) -> tuple[Path, Path]:
        runtime = root / "runtime"
        stocks = root / "stocks"
        runtime.mkdir()
        stocks.mkdir()
        self._write_json(runtime / "operation_state.json", {
            "operation_date": operation_date,
            "operation_status": "CLOSING",
            "operation_participant_stock_codes": ["111111"],
        })
        self._write_json(runtime / "order_queue.json", {"version": 1, "orders": []})
        self._write_json(runtime / "positions.json", {"positions": []})
        self._write_json(runtime / "broker_holdings.json", {"broker_holdings": []})
        stock_dir = stocks / "111111_Test"
        stock_dir.mkdir()
        self._write_json(stock_dir / "state.json", {"status": "AUTO_CLOSED", "holding_qty": 0})
        self._write_json(stock_dir / "orders.json", {"orders": []})
        return runtime, stocks

    def test_service_calls_evaluator_once_and_returns_source(self) -> None:
        calls = []

        def evaluator(**kwargs):
            calls.append(kwargs)
            return {"global_complete": True, "reasons": ["ok"]}

        result = check_global_close_completion_after_durable_update(
            source=SOURCE_ORDER_FILL_STATE_COMMIT,
            evaluator=evaluator,
            custom_path="value",
        )

        self.assertTrue(result["checked"])
        self.assertEqual(SOURCE_ORDER_FILL_STATE_COMMIT, result["source"])
        self.assertTrue(result["global_complete"])
        self.assertEqual(["ok"], result["reasons"])
        self.assertEqual(1, len(calls))
        self.assertEqual("value", calls[0]["custom_path"])

    def test_service_preserves_false_result(self) -> None:
        result = check_global_close_completion_after_durable_update(
            source=SOURCE_ORDER_FILL_STATE_COMMIT,
            evaluator=lambda **_kwargs: {"global_complete": False, "reasons": ["pending"]},
        )

        self.assertTrue(result["checked"])
        self.assertFalse(result["global_complete"])
        self.assertEqual(["pending"], result["reasons"])

    def test_service_exception_is_check_failed(self) -> None:
        def evaluator(**_kwargs):
            raise RuntimeError("boom")

        result = check_global_close_completion_after_durable_update(
            source=SOURCE_ORDER_FILL_STATE_COMMIT,
            evaluator=evaluator,
        )

        self.assertFalse(result["checked"])
        self.assertTrue(result["check_failed"])
        self.assertFalse(result["global_complete"])
        self.assertEqual(["boom"], result["reasons"])

    def test_incomplete_service_check_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, stocks = self._runtime_root(root)
            self._write_json(
                stocks / "111111_Test" / "state.json",
                {"status": "AUTO_CLOSING", "holding_qty": 1},
            )
            before = self._hashes(root)

            result = check_global_close_completion_after_durable_update(
                source=SOURCE_STARTUP_RECOVERY,
                today="2026-07-30",
                operation_state_path=runtime / "operation_state.json",
                stocks_dir=stocks,
                order_queue_path=runtime / "order_queue.json",
                positions_path=runtime / "positions.json",
                broker_holdings_path=runtime / "broker_holdings.json",
            )

            self.assertFalse(result["global_complete"])
            self.assertFalse(result["normal_ended_applied"])
            self.assertEqual(before, self._hashes(root))

    def test_complete_service_writes_normal_ended(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, stocks = self._runtime_root(root)
            self._write_json(runtime / "operation_state.json", {
                "operation_date": "2026-07-30",
                "operation_status": "CLOSING",
                "operation_started_at": "2026-07-30 09:01:00",
                "operation_closing_started_at": "2026-07-30 15:20:00",
                "operation_close_reason": "AUTO_CLOSE",
                "operation_participant_stock_codes": ["111111"],
                "emergency_stop": False,
                "existing_key": "preserve",
            })

            result = check_global_close_completion_after_durable_update(
                source=SOURCE_STARTUP_RECOVERY,
                today="2026-07-30",
                normal_end_timestamp="2026-07-30 15:31:00",
                operation_state_path=runtime / "operation_state.json",
                stocks_dir=stocks,
                order_queue_path=runtime / "order_queue.json",
                positions_path=runtime / "positions.json",
                broker_holdings_path=runtime / "broker_holdings.json",
            )
            state = json.loads((runtime / "operation_state.json").read_text(encoding="utf-8"))

        self.assertTrue(result["global_complete"])
        self.assertTrue(result["normal_ended_applied"])
        self.assertFalse(result["normal_end_write_failed"])
        self.assertEqual("NORMAL_ENDED", result["operation_status_after"])
        self.assertEqual("NORMAL_ENDED", state["operation_status"])
        self.assertEqual("2026-07-30 15:31:00", state["operation_ended_at"])
        self.assertEqual("ALL_PARTICIPANTS_COMPLETE", state["operation_end_reason"])
        self.assertEqual("2026-07-30 09:01:00", state["operation_started_at"])
        self.assertEqual("2026-07-30 15:20:00", state["operation_closing_started_at"])
        self.assertEqual("AUTO_CLOSE", state["operation_close_reason"])
        self.assertEqual(["111111"], state["operation_participant_stock_codes"])
        self.assertFalse(state["emergency_stop"])
        self.assertEqual("preserve", state["existing_key"])

    def test_done_and_carryover_done_writes_normal_ended(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, stocks = self._runtime_root(root)
            self._write_json(runtime / "operation_state.json", {
                "operation_date": "2026-07-30",
                "operation_status": "CLOSING",
                "operation_participant_stock_codes": ["111111", "222222"],
            })
            stock_dir = stocks / "222222_Test"
            stock_dir.mkdir()
            self._write_json(stock_dir / "state.json", {
                "status": "AUTO_CLOSING",
                "holding_qty": 5,
                "operation_notice": "CARRYOVER_DONE",
            })
            self._write_json(stock_dir / "orders.json", {"orders": []})

            result = check_global_close_completion_after_durable_update(
                source=SOURCE_STARTUP_RECOVERY,
                today="2026-07-30",
                normal_end_timestamp="2026-07-30 15:35:00",
                operation_state_path=runtime / "operation_state.json",
                stocks_dir=stocks,
                order_queue_path=runtime / "order_queue.json",
                positions_path=runtime / "positions.json",
                broker_holdings_path=runtime / "broker_holdings.json",
            )

        self.assertTrue(result["normal_ended_applied"])
        self.assertEqual(
            {"DONE": 1, "CARRYOVER_DONE": 1},
            result["evaluator_result"]["status_counts"],
        )

    def test_blocking_stock_statuses_do_not_write_normal_ended(self) -> None:
        blocking_statuses = [
            STATUS_PENDING_ORDER,
            STATUS_HOLDING_REMAINS,
            STATUS_REVIEW_REQUIRED,
            STATUS_CLOSE_NOT_STARTED,
            STATUS_EVIDENCE_CONFLICT,
            STATUS_UNKNOWN,
        ]
        for status in blocking_statuses:
            with self.subTest(status=status):
                calls = []

                def writer(**kwargs):
                    calls.append(kwargs)
                    return {"ok": True}

                def evaluator(**_kwargs):
                    return {
                        "blocked": False,
                        "global_complete": False,
                        "operation_date": "2026-07-30",
                        "operation_status": "CLOSING",
                        "participant_stock_codes": ["111111"],
                        "blocking_stock_codes": ["111111"],
                        "stock_results": [{"stock_code": "111111", "status": status}],
                        "reasons": [status],
                    }

                result = check_global_close_completion_after_durable_update(
                    source=SOURCE_ORDER_FILL_STATE_COMMIT,
                    evaluator=evaluator,
                    normal_end_writer=writer,
                )

                self.assertFalse(result["normal_ended_applied"])
                self.assertEqual([], calls)

    def test_evaluator_blocked_or_exception_does_not_write_normal_ended(self) -> None:
        calls = []

        result = check_global_close_completion_after_durable_update(
            source=SOURCE_ORDER_FILL_STATE_COMMIT,
            evaluator=lambda **_kwargs: {
                "blocked": True,
                "global_complete": False,
                "operation_status": "CLOSING",
                "participant_stock_codes": ["111111"],
                "stock_results": [],
                "blocking_stock_codes": [],
                "reasons": ["blocked"],
            },
            normal_end_writer=lambda **kwargs: calls.append(kwargs) or {"ok": True},
        )
        self.assertFalse(result["normal_ended_applied"])
        self.assertEqual([], calls)

        def broken(**_kwargs):
            raise RuntimeError("boom")

        result = check_global_close_completion_after_durable_update(
            source=SOURCE_ORDER_FILL_STATE_COMMIT,
            evaluator=broken,
            normal_end_writer=lambda **kwargs: calls.append(kwargs) or {"ok": True},
        )
        self.assertTrue(result["check_failed"])
        self.assertFalse(result["normal_ended_applied"])
        self.assertEqual([], calls)

    def test_running_or_empty_participants_preserves_file(self) -> None:
        cases = [
            {"operation_status": "RUNNING", "operation_participant_stock_codes": ["111111"]},
            {"operation_status": "CLOSING", "operation_participant_stock_codes": []},
        ]
        for extra in cases:
            with self.subTest(extra=extra):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    runtime, stocks = self._runtime_root(root)
                    self._write_json(runtime / "operation_state.json", {
                        "operation_date": "2026-07-30",
                        **extra,
                    })
                    before = self._hashes(root)

                    result = check_global_close_completion_after_durable_update(
                        source=SOURCE_STARTUP_RECOVERY,
                        today="2026-07-30",
                        operation_state_path=runtime / "operation_state.json",
                        stocks_dir=stocks,
                        order_queue_path=runtime / "order_queue.json",
                        positions_path=runtime / "positions.json",
                        broker_holdings_path=runtime / "broker_holdings.json",
                    )

                    self.assertFalse(result["normal_ended_applied"])
                    self.assertEqual(before, self._hashes(root))

    def test_writer_failure_is_reported_without_raising(self) -> None:
        result = check_global_close_completion_after_durable_update(
            source=SOURCE_ORDER_FILL_STATE_COMMIT,
            evaluator=lambda **_kwargs: {
                "blocked": False,
                "global_complete": True,
                "operation_date": "2026-07-30",
                "operation_status": "CLOSING",
                "participant_stock_codes": ["111111"],
                "blocking_stock_codes": [],
                "stock_results": [{"stock_code": "111111", "status": STATUS_DONE}],
                "reasons": [],
            },
            normal_end_writer=lambda **_kwargs: {"ok": False, "reason": "write failed"},
        )

        self.assertTrue(result["global_complete"])
        self.assertFalse(result["normal_ended_applied"])
        self.assertTrue(result["normal_end_write_failed"])
        self.assertEqual("write failed", result["normal_end_write"]["reason"])

    def test_normal_ended_writer_is_idempotent_and_preserves_first_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtime" / "operation_state.json"
            self._write_json(path, {
                "operation_date": "2026-07-30",
                "operation_status": "NORMAL_ENDED",
                "operation_started_at": "2026-07-30 09:00:00",
                "operation_ended_at": "2026-07-30 15:30:00",
                "operation_end_reason": "ALL_PARTICIPANTS_COMPLETE",
                "operation_participant_stock_codes": ["111111"],
                "emergency_stop": False,
                "unknown_key": "preserve",
            })

            result = operation_policy_gate.write_global_operation_normal_ended_state(
                timestamp="2026-07-30 16:00:00",
                operation_state_path=path,
            )
            state = json.loads(path.read_text(encoding="utf-8"))

        self.assertTrue(result["ok"])
        self.assertEqual("2026-07-30 15:30:00", state["operation_ended_at"])
        self.assertEqual("ALL_PARTICIPANTS_COMPLETE", state["operation_end_reason"])
        self.assertEqual("2026-07-30 16:00:00", state["operation_updated_at"])
        self.assertEqual("preserve", state["unknown_key"])

    def test_order_fill_commit_success_runs_completion_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "runtime" / "order_queue.json"
            self._write_json(queue_path, {
                "version": 1,
                "updated_at": "before",
                "orders": [{
                    "id": "ORDER_QUEUED_ORDER_1",
                    "status": "ORDER_QUEUED",
                    "order_id": "ORDER_1",
                    "request_hash": "HASH_1",
                    "lock_id": "LOCK_1",
                    "execution_id": "EXEC_1",
                    "total_filled_quantity": 0,
                    "remaining_quantity": 10,
                }],
            })
            review = {
                "order_fill_state_review_ok": True,
                "next_stage": "ORDER_FILL_STATE_COMMIT_REQUIRED",
                "status_candidate": "FILLED",
                "event_type": "FULL_FILL",
                "order_id": "ORDER_1",
                "order_queued_id": "ORDER_QUEUED_ORDER_1",
                "fill_id": "FILL_1",
                "total_filled_quantity_candidate": 10,
                "remaining_quantity_candidate": 0,
            }
            with patch.object(
                order_fill_state_service,
                "check_global_close_completion_for_runtime_path",
                return_value={"checked": True, "source": SOURCE_ORDER_FILL_STATE_COMMIT},
            ) as check:
                result = order_fill_state_service.commit_order_fill_state(
                    review,
                    queue_path,
                    context={"manual_order_fill_state_commit_confirmed": True},
                    backup=False,
                )

        self.assertTrue(result["order_fill_state_committed"])
        self.assertEqual(SOURCE_ORDER_FILL_STATE_COMMIT, result["completion_check_result"]["source"])
        check.assert_called_once()

    def test_order_fill_commit_confirmed_completion_writes_normal_ended(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, stocks = self._runtime_root(root, operation_date=today_text())
            queue_path = runtime / "order_queue.json"
            self._write_json(queue_path, {
                "version": 1,
                "updated_at": "before",
                "orders": [{
                    "id": "ORDER_QUEUED_ORDER_1",
                    "status": "ORDER_QUEUED",
                    "code": "111111",
                    "order_id": "ORDER_1",
                    "request_hash": "HASH_1",
                    "lock_id": "LOCK_1",
                    "execution_id": "EXEC_1",
                    "total_filled_quantity": 0,
                    "remaining_quantity": 10,
                }],
            })
            review = {
                "order_fill_state_review_ok": True,
                "next_stage": "ORDER_FILL_STATE_COMMIT_REQUIRED",
                "status_candidate": "FILLED",
                "event_type": "FULL_FILL",
                "order_id": "ORDER_1",
                "order_queued_id": "ORDER_QUEUED_ORDER_1",
                "fill_id": "FILL_1",
                "total_filled_quantity_candidate": 10,
                "remaining_quantity_candidate": 0,
            }
            result = order_fill_state_service.commit_order_fill_state(
                review,
                queue_path,
                context={"manual_order_fill_state_commit_confirmed": True},
                backup=False,
            )
            state = json.loads((runtime / "operation_state.json").read_text(encoding="utf-8"))

        self.assertTrue(result["order_fill_state_committed"])
        self.assertTrue(result["completion_check_result"]["normal_ended_applied"])
        self.assertEqual("NORMAL_ENDED", state["operation_status"])
        self.assertEqual("ALL_PARTICIPANTS_COMPLETE", state["operation_end_reason"])

    def test_order_fill_commit_failure_does_not_run_completion_check(self) -> None:
        with patch.object(order_fill_state_service, "check_global_close_completion_for_runtime_path") as check:
            result = order_fill_state_service.commit_order_fill_state(
                {"order_fill_state_review_ok": False},
                None,
            )

        self.assertFalse(result["order_fill_state_committed"])
        check.assert_not_called()

    def test_broker_holding_commit_success_runs_completion_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            holdings_path = Path(tmp) / "runtime" / "broker_holdings.json"
            positions_path = Path(tmp) / "runtime" / "positions.json"
            self._write_json(positions_path, {"version": 1, "updated_at": "before", "positions": []})
            raw_event = {
                "source": "kiwoom_chejan",
                "gubun": "1",
                "received_at": "2026-07-16 11:00:00",
                "fid_values": {
                    "9201": "12345678",
                    "9001": "A003550",
                    "302": "LG",
                    "930": "0",
                    "933": "0",
                    "931": "0",
                    "932": "0",
                },
            }
            context = {"kiwoom_api_live_event": True, "live_event_source": "KiwoomApi.raw_chejan_received"}
            with patch.object(
                broker_holding_recorder,
                "check_global_close_completion_for_runtime_path",
                return_value={"checked": True, "source": SOURCE_BROKER_HOLDING_COMMIT},
            ) as check:
                result = broker_holding_recorder.record_broker_holding_snapshot(
                    raw_event,
                    holdings_path,
                    positions_path,
                    context=context,
                    backup=False,
                )

        self.assertTrue(result["holding_recorded"])
        self.assertEqual(SOURCE_BROKER_HOLDING_COMMIT, result["completion_check_result"]["source"])
        check.assert_called_once()

    def test_broker_holding_commit_failure_does_not_run_completion_check(self) -> None:
        with patch.object(broker_holding_recorder, "check_global_close_completion_for_runtime_path") as check:
            result = broker_holding_recorder.record_broker_holding_snapshot(
                {"gubun": "0"},
                "missing.json",
                "positions.json",
                context={},
            )

        self.assertFalse(result.get("holding_recorded", False))
        check.assert_not_called()

    def test_early_close_durable_update_success_runs_completion_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stock_dir = Path(tmp) / "stocks" / "111111_Test"
            stock_dir.mkdir(parents=True)
            self._write_json(stock_dir / "state.json", {
                "status": "EARLY_CLOSING",
                "early_close_requested_at": "2026-07-30 13:30:00",
                "operation_command_id": "CMD-1",
            })
            self._write_json(stock_dir / "config.json", {"assigned_routine_instance_id": "routine-1"})
            window = SimpleNamespace()
            with (
                patch("gui_auto_trade_runtime.all_registered_stock_dirs", return_value=[stock_dir]),
                patch.object(gui_auto_trade_close, "_production_recovery_gate", return_value=None),
                patch.object(gui_auto_trade_close, "_start_close_liquidation_execution", return_value={"ok": True, "runtime_status": "EARLY_CLOSED"}),
                patch.object(gui_auto_trade_close, "_persist_early_close_execution_result", return_value=True),
                patch.object(
                    gui_auto_trade_close,
                    "check_global_close_completion_after_durable_update",
                    return_value={"checked": True, "source": SOURCE_EARLY_CLOSE_DURABLE_UPDATE},
                ) as check,
            ):
                result = gui_auto_trade_close.auto_trade_continue_pending_close_liquidations(window)

        self.assertEqual(1, result["processed"])
        self.assertEqual(SOURCE_EARLY_CLOSE_DURABLE_UPDATE, result["results"][0]["completion_check_result"]["source"])
        check.assert_called_once()

    def test_early_close_durable_update_failure_does_not_run_completion_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stock_dir = Path(tmp) / "stocks" / "111111_Test"
            stock_dir.mkdir(parents=True)
            self._write_json(stock_dir / "state.json", {
                "status": "EARLY_CLOSING",
                "early_close_requested_at": "2026-07-30 13:30:00",
                "operation_command_id": "CMD-1",
            })
            self._write_json(stock_dir / "config.json", {"assigned_routine_instance_id": "routine-1"})
            with (
                patch("gui_auto_trade_runtime.all_registered_stock_dirs", return_value=[stock_dir]),
                patch.object(gui_auto_trade_close, "_production_recovery_gate", return_value=None),
                patch.object(gui_auto_trade_close, "_start_close_liquidation_execution", return_value={"ok": True, "runtime_status": "EARLY_CLOSED"}),
                patch.object(gui_auto_trade_close, "_persist_early_close_execution_result", return_value=False),
                patch.object(gui_auto_trade_close, "check_global_close_completion_after_durable_update") as check,
            ):
                result = gui_auto_trade_close.auto_trade_continue_pending_close_liquidations(SimpleNamespace())

        self.assertEqual(0, result["processed"])
        check.assert_not_called()

    def test_startup_recovery_closing_runs_completion_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, stocks = self._runtime_root(root, operation_date=today_text())
            with patch.object(
                operator_reconciliation_service,
                "check_global_close_completion_after_durable_update",
                return_value={"checked": True, "source": SOURCE_STARTUP_RECOVERY},
            ) as check:
                result = operator_reconciliation_service.assess_startup_recovery(
                    queue_path=runtime / "order_queue.json",
                    fills_path=runtime / "fills.json",
                    positions_path=runtime / "positions.json",
                    broker_holdings_path=runtime / "broker_holdings.json",
                    order_executions_path=runtime / "order_executions.json",
                    order_locks_path=runtime / "order_locks.json",
                    routine_signals_path=runtime / "routine_signals.json",
                    stock_state_paths=[stocks / "111111_Test" / "state.json"],
                )

        self.assertEqual(SOURCE_STARTUP_RECOVERY, result["completion_check_result"]["source"])
        check.assert_called_once()

    def test_startup_recovery_closing_complete_writes_normal_ended(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, stocks = self._runtime_root(root, operation_date=today_text())
            result = operator_reconciliation_service.assess_startup_recovery(
                queue_path=runtime / "order_queue.json",
                fills_path=runtime / "fills.json",
                positions_path=runtime / "positions.json",
                broker_holdings_path=runtime / "broker_holdings.json",
                order_executions_path=runtime / "order_executions.json",
                order_locks_path=runtime / "order_locks.json",
                routine_signals_path=runtime / "routine_signals.json",
                stock_state_paths=[stocks / "111111_Test" / "state.json"],
            )
            state = json.loads((runtime / "operation_state.json").read_text(encoding="utf-8"))

        self.assertEqual(SOURCE_STARTUP_RECOVERY, result["completion_check_result"]["source"])
        self.assertTrue(result["completion_check_result"]["normal_ended_applied"])
        self.assertEqual("NORMAL_ENDED", state["operation_status"])

    def test_startup_recovery_running_does_not_run_completion_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, stocks = self._runtime_root(root)
            self._write_json(runtime / "operation_state.json", {
                "operation_date": "2026-07-30",
                "operation_status": "RUNNING",
                "operation_participant_stock_codes": ["111111"],
            })
            with patch.object(operator_reconciliation_service, "check_global_close_completion_after_durable_update") as check:
                result = operator_reconciliation_service.assess_startup_recovery(
                    queue_path=runtime / "order_queue.json",
                    fills_path=runtime / "fills.json",
                    positions_path=runtime / "positions.json",
                    broker_holdings_path=runtime / "broker_holdings.json",
                    order_executions_path=runtime / "order_executions.json",
                    order_locks_path=runtime / "order_locks.json",
                    routine_signals_path=runtime / "routine_signals.json",
                    stock_state_paths=[stocks / "111111_Test" / "state.json"],
                )

        self.assertNotIn("completion_check_result", result)
        check.assert_not_called()


if __name__ == "__main__":
    unittest.main()
