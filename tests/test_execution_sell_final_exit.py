# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from execution_provenance_contract import materialize_execution_intent_children
from execution_sell_final_exit import inspect_sell_final_residual_exits
import gui_auto_trade_timer
import routine_signal_consumer


ACCOUNT = "81291234"
CODE = "005930"
SIGNAL = "SIGNAL-FINAL-EXIT-1"
PROCESS = "PROCESS-FINAL-EXIT-1"
EXIT_HASH = "EXIT-SNAPSHOT-HASH"


class ExecutionSellFinalExitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.queue = self.root / "order_queue.json"
        self.executions = self.root / "order_executions.json"
        self.fills = self.root / "fills.json"
        self.positions = self.root / "positions.json"
        self.holdings = self.root / "broker_holdings.json"
        self.signals = self.root / "routine_signals.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _write(path: Path, **fields: object) -> None:
        path.write_text(json.dumps({"version": 1, **fields}), encoding="utf-8")

    @staticmethod
    def _initial_intent() -> dict[str, object]:
        values = materialize_execution_intent_children(
            [
                {
                    "side": "SELL",
                    "budget": None,
                    "routine_type": "INDICATOR_FOLLOW",
                    "routine_instance_id": "INSTANCE-1",
                    "source_signal_id": SIGNAL,
                    "execution_process_id": PROCESS,
                    "execution_process_owner_required": False,
                    "option_snapshot_hash": "OPTION-HASH",
                    "plan_generation": 0,
                    "planned_total_quantity": 10,
                    "execution_mode": "SINGLE_ORDER",
                    "quantity": 10,
                    "price": 100,
                    "hoga": "LIMIT",
                    "price_basis": "ORDER_PRICE",
                    "child_sequence_index": 1,
                    "child_sequence_total": 1,
                    "child_kind": "SINGLE_ORDER",
                    "child_plan": {"planned_quantity": 10, "planned_price": 100},
                    "sell_repeat_policy": {
                        "policy": "SELL_FOLLOW_UP_REPEAT",
                        "enabled": True,
                        "execution_template": {
                            "execution_mode": "SINGLE_ORDER",
                            "hoga": "LIMIT",
                            "price_basis": "ORDER_PRICE",
                        },
                    },
                    "sell_price_reset_policy": {
                        "policy": "SELL_PRICE_CHANGE_RESET",
                        "action": "RESET",
                    },
                    "unfilled_timeout_policy": {
                        "policy": "SELL_UNFILLED_TIMEOUT_CANCEL",
                        "scope": "EACH",
                        "timeout_ms": 10_000,
                    },
                }
            ],
            source_signal_id=SIGNAL,
            execution_process_id=PROCESS,
            plan_generation_value=0,
        )
        return values[0]

    def _fixture(
        self,
        *,
        status: str = "FILLED",
        holding: int = 3,
        available: int | None = None,
        position: int | None = None,
        holding_received_at: str = "2026-09-03T10:02:00",
        extra_orders: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        intent = self._initial_intent()
        order = {
            "id": "ORDER-INITIAL",
            "status": status,
            "source_signal_id": SIGNAL,
            "execution_process_id": PROCESS,
            "execution_id": intent["execution_id"],
            "plan_generation": 0,
            "option_snapshot_hash": "OPTION-HASH",
            "account_no": ACCOUNT,
            "code": CODE,
            "name": "삼성전자",
            "side": "SELL",
            "order_action": "NEW",
            "broker_order_no": "BROKER-INITIAL",
            "remaining_quantity": 10 if status in {"BROKER_ACCEPTED", "PARTIALLY_FILLED"} else 0,
            "quantity": 10,
            "execution_intent": deepcopy(intent),
            "updated_at": "2026-09-03T10:00:00",
        }
        self._write(self.queue, orders=[order, *(extra_orders or [])])
        self._write(
            self.executions,
            executions=[
                {
                    "execution_id": intent["execution_id"],
                    "execution_process_id": PROCESS,
                    "plan_generation": 0,
                }
            ],
            processes=[
                {
                    "execution_process_id": PROCESS,
                    "option_snapshot_hash": "OPTION-HASH",
                }
            ],
        )
        self._write(self.fills, fills=[])
        self._write(
            self.positions,
            positions=[
                {
                    "account_no": ACCOUNT,
                    "code": CODE,
                    "quantity": holding if position is None else position,
                    "average_price": 95,
                    "updated_at": "2026-09-03T10:01:00",
                }
            ],
        )
        self._write(
            self.holdings,
            holdings=[
                {
                    "account_no": ACCOUNT,
                    "code": CODE,
                    "holding_quantity": holding,
                    "available_quantity": holding if available is None else available,
                    "received_at": holding_received_at,
                    "reconciliation_status": "CONSISTENT",
                    "manual_reconciliation_required": False,
                }
            ],
        )
        self._write(
            self.signals,
            signals=[
                {
                    "id": SIGNAL,
                    "routine": "지표추종매매",
                    "routine_instance_id": "INSTANCE-1",
                    "code": CODE,
                    "name": "삼성전자",
                    "signal": "SELL",
                    "status": "PREVIEWED",
                    "execution_intent": deepcopy(intent),
                    "execution_intents": [deepcopy(intent)],
                    "sell_repeat_exit_evidence": {
                        "policy": "SELL_REPEAT_EXIT",
                        "execution_process_id": PROCESS,
                        "source_signal_id": SIGNAL,
                        "exit_triggered_at": "2026-09-03T10:01:00",
                        "exit_source_snapshot_hash": EXIT_HASH,
                        "exit_source_snapshot": {"snapshot_hash": EXIT_HASH},
                        "evaluated_generation": 0,
                    },
                }
            ],
        )
        return self._inspect()

    def _inspect(self) -> dict[str, object]:
        return inspect_sell_final_residual_exits(
            selected_account_no=ACCOUNT,
            allowed_stock_codes=(CODE,),
            now=datetime.fromisoformat("2026-09-03T10:03:00"),
            order_queue_path=self.queue,
            order_executions_path=self.executions,
            fills_path=self.fills,
            positions_path=self.positions,
            holdings_path=self.holdings,
            signals_path=self.signals,
        )

    def _install_final_order(
        self,
        proposal: dict[str, object],
        *,
        status: str,
        updated_at: str = "2026-09-03T10:03:30",
    ) -> None:
        intent = deepcopy(proposal["execution_intents"][0])
        queue = json.loads(self.queue.read_text(encoding="utf-8"))
        queue["orders"].append(
            {
                "id": "ORDER-FINAL",
                "status": status,
                "source_signal_id": SIGNAL,
                "execution_process_id": PROCESS,
                "execution_id": intent["execution_id"],
                "plan_generation": intent["plan_generation"],
                "option_snapshot_hash": "OPTION-HASH",
                "account_no": ACCOUNT,
                "code": CODE,
                "name": "삼성전자",
                "side": "SELL",
                "order_action": "NEW",
                "broker_order_no": "BROKER-FINAL",
                "remaining_quantity": 0 if status == "FILLED" else 3,
                "quantity": intent["quantity"],
                "execution_intent": intent,
                "updated_at": updated_at,
            }
        )
        self.queue.write_text(json.dumps(queue), encoding="utf-8")
        executions = json.loads(self.executions.read_text(encoding="utf-8"))
        executions["executions"].append(
            {
                "execution_id": intent["execution_id"],
                "execution_process_id": PROCESS,
                "plan_generation": intent["plan_generation"],
            }
        )
        self.executions.write_text(json.dumps(executions), encoding="utf-8")
        signals = json.loads(self.signals.read_text(encoding="utf-8"))
        signals["signals"][0]["status"] = "PREVIEWED"
        signals["signals"][0]["execution_intent"] = deepcopy(intent)
        signals["signals"][0]["execution_intents"] = [deepcopy(intent)]
        signals["signals"][0]["final_residual_exit_evidence"] = {
            "status": "ORDER_QUEUED",
            "final_residual_exit_action_hash": proposal[
                "final_residual_exit_action_hash"
            ],
        }
        self.signals.write_text(json.dumps(signals), encoding="utf-8")

    def test_durable_exit_builds_one_market_sell_from_latest_available(self) -> None:
        result = self._fixture(holding=5, available=3)
        self.assertEqual(result["reviews"], [])
        self.assertEqual(len(result["proposals"]), 1)
        proposal = result["proposals"][0]
        intent = proposal["execution_intents"][0]
        self.assertEqual(proposal["latest_sellable_quantity"], 3)
        self.assertEqual(intent["quantity"], 3)
        self.assertEqual(intent["side"], "SELL")
        self.assertEqual(intent["hoga"], "MARKET")
        self.assertEqual(intent["price_basis"], "MARKET")
        self.assertIsNone(intent["price"])
        self.assertEqual(intent["child_kind"], "SINGLE_ORDER")
        self.assertEqual(intent["source_signal_id"], SIGNAL)
        self.assertEqual(intent["execution_process_id"], PROCESS)
        self.assertEqual(intent["plan_generation"], 1)
        self.assertTrue(intent["final_residual_exit"])
        self.assertNotIn("repeat_source_snapshot_hash", intent)
        self.assertNotIn("sell_repeat_policy", intent)
        self.assertNotIn("sell_price_reset_policy", intent)
        self.assertNotIn("unfilled_timeout_policy", intent)

    def test_holding_zero_records_normal_completion_without_order(self) -> None:
        result = self._fixture(holding=0, available=0, position=0)
        self.assertEqual(result["proposals"], [])
        self.assertEqual(len(result["completion_proposals"]), 1)
        self.assertEqual(
            result["completion_proposals"][0]["reason"],
            "SELL_FINAL_EXIT_NOT_REQUIRED_HOLDING_ZERO",
        )

    def test_holding_zero_completion_is_idempotent_after_restart(self) -> None:
        completion = self._fixture(holding=0, available=0, position=0)[
            "completion_proposals"
        ][0]
        signals = json.loads(self.signals.read_text(encoding="utf-8"))
        signals["signals"][0]["status"] = "DONE"
        signals["signals"][0]["final_residual_exit_evidence"] = {
            "status": "HOLDING_ZERO_CONFIRMED",
            "final_residual_exit_action_hash": completion[
                "final_residual_exit_action_hash"
            ],
            "resulting_holding_zero_confirmed": True,
        }
        self.signals.write_text(json.dumps(signals), encoding="utf-8")
        result = self._inspect()
        self.assertEqual(result["proposals"], [])
        self.assertEqual(result["completion_proposals"], [])
        self.assertEqual(result["reviews"], [])
        self.assertIn(
            "SELL_FINAL_EXIT_COMPLETE",
            {item["reason"] for item in result["waiting"]},
        )

    def test_stale_holding_waits(self) -> None:
        result = self._fixture(holding_received_at="2026-09-03T10:00:30")
        self.assertEqual(result["proposals"], [])
        self.assertIn(
            "SELL_FINAL_EXIT_HOLDING_EVIDENCE_PENDING",
            {item["reason"] for item in result["waiting"]},
        )

    def test_open_order_and_active_cancel_prevent_final_sell(self) -> None:
        open_result = self._fixture(status="BROKER_ACCEPTED")
        self.assertEqual(open_result["proposals"], [])
        self.assertIn(
            "SELL_FINAL_EXIT_OPEN_SELL_ORDER",
            {item["reason"] for item in open_result["waiting"]},
        )
        cancel = {
            "id": "ORDER-CANCEL",
            "status": "BROKER_ACCEPTED",
            "source_signal_id": SIGNAL,
            "execution_process_id": PROCESS,
            "account_no": ACCOUNT,
            "code": CODE,
            "side": "SELL",
            "order_action": "CANCEL",
            "original_order_no": "BROKER-INITIAL",
            "updated_at": "2026-09-03T10:00:30",
        }
        cancel_result = self._fixture(extra_orders=[cancel])
        self.assertEqual(cancel_result["proposals"], [])
        self.assertIn(
            "SELL_FINAL_EXIT_ACTIVE_CANCEL",
            {item["reason"] for item in cancel_result["waiting"]},
        )

    def test_uncertain_and_position_mismatch_are_isolated(self) -> None:
        uncertain = self._fixture(status="SEND_UNCERTAIN")
        self.assertEqual(uncertain["proposals"], [])
        self.assertTrue(
            any(
                "SELL_FINAL_EXIT_UNSAFE_ORDER" in reason
                for review in uncertain["reviews"]
                for reason in review["review_reasons"]
            )
        )
        mismatch = self._fixture(holding=3, position=4)
        self.assertEqual(mismatch["proposals"], [])
        self.assertIn(
            "SELL_FINAL_EXIT_POSITION_BROKER_MISMATCH",
            mismatch["reviews"][0]["review_reasons"],
        )

    def test_existing_final_order_is_never_duplicated(self) -> None:
        proposal = self._fixture()["proposals"][0]
        self._install_final_order(proposal, status="BROKER_ACCEPTED")
        result = self._inspect()
        self.assertEqual(result["proposals"], [])
        self.assertIn(
            "SELL_FINAL_EXIT_ORDER_ACTIVE",
            {item["reason"] for item in result["waiting"]},
        )

    def test_signal_writer_crash_window_waits_for_generic_consumer(self) -> None:
        proposal = self._fixture()["proposals"][0]
        intent = deepcopy(proposal["execution_intents"][0])
        signals = json.loads(self.signals.read_text(encoding="utf-8"))
        signals["signals"][0]["status"] = "PENDING"
        signals["signals"][0]["execution_intent"] = intent
        signals["signals"][0]["execution_intents"] = [intent]
        signals["signals"][0]["final_residual_exit_evidence"] = {
            "status": "REQUESTED",
            "final_residual_exit_action_hash": proposal[
                "final_residual_exit_action_hash"
            ],
        }
        self.signals.write_text(json.dumps(signals), encoding="utf-8")
        result = self._inspect()
        self.assertEqual(result["proposals"], [])
        self.assertEqual(result["reviews"], [])
        self.assertIn(
            "SELL_FINAL_EXIT_GENERATION_PENDING_EXECUTION",
            {item["reason"] for item in result["waiting"]},
        )

    def test_newer_repeat_generation_after_exit_is_reconciled(self) -> None:
        self._fixture()
        signals = json.loads(self.signals.read_text(encoding="utf-8"))
        newer = deepcopy(signals["signals"][0]["execution_intent"])
        newer["plan_generation"] = 1
        newer["repeat_source_snapshot_hash"] = "ILLEGAL-POST-EXIT-REPEAT"
        signals["signals"][0]["execution_intent"] = newer
        signals["signals"][0]["execution_intents"] = [newer]
        self.signals.write_text(json.dumps(signals), encoding="utf-8")
        result = self._inspect()
        self.assertEqual(result["proposals"], [])
        self.assertIn(
            "SELL_FINAL_EXIT_NEWER_REPEAT_SIGNAL_INTENT_EXISTS",
            result["reviews"][0]["review_reasons"],
        )

    def test_final_fill_requires_fresh_holding_zero_confirmation(self) -> None:
        proposal = self._fixture()["proposals"][0]
        self._install_final_order(proposal, status="FILLED")
        self._write(
            self.positions,
            positions=[
                {
                    "account_no": ACCOUNT,
                    "code": CODE,
                    "quantity": 0,
                    "updated_at": "2026-09-03T10:03:35",
                }
            ],
        )
        self._write(
            self.holdings,
            holdings=[
                {
                    "account_no": ACCOUNT,
                    "code": CODE,
                    "holding_quantity": 0,
                    "available_quantity": 0,
                    "received_at": "2026-09-03T10:04:00",
                    "reconciliation_status": "CONSISTENT",
                    "manual_reconciliation_required": False,
                }
            ],
        )
        result = self._inspect()
        self.assertEqual(result["proposals"], [])
        self.assertEqual(len(result["completion_proposals"]), 1)
        self.assertEqual(
            result["completion_proposals"][0]["final_execution_id"],
            proposal["execution_intents"][0]["execution_id"],
        )

    def test_final_market_reject_is_review_not_blind_retry(self) -> None:
        proposal = self._fixture()["proposals"][0]
        self._install_final_order(proposal, status="BROKER_REJECTED")
        result = self._inspect()
        self.assertEqual(result["proposals"], [])
        self.assertTrue(
            any(
                "SELL_FINAL_EXIT_MARKET_ORDER_REJECTED" in reason
                for review in result["reviews"]
                for reason in review["review_reasons"]
            )
        )

    def test_consumer_uses_existing_writer_and_generic_pipeline(self) -> None:
        proposal = self._fixture()["proposals"][0]
        with mock.patch.object(
            routine_signal_consumer,
            "update_signal_status",
            side_effect=[{"ok": True}, {"ok": True}],
        ) as writer, mock.patch.object(
            routine_signal_consumer,
            "enqueue_replanned_execution_intents",
            return_value={
                "ok": True,
                "orders_created": 1,
                "executable_order_ids": ["ORDER-FINAL"],
                "append_result": {"created_orders": [{"id": "ORDER-FINAL"}]},
            },
        ) as enqueue:
            result = routine_signal_consumer.enqueue_final_residual_sell_exit(proposal)
        self.assertTrue(result["ok"])
        self.assertEqual(writer.call_count, 2)
        enqueue.assert_called_once()
        first_intent = enqueue.call_args.args[1][0]
        self.assertEqual(first_intent["hoga"], "MARKET")
        self.assertIsNone(first_intent["price"])

    def test_final_market_reenters_admission_approval_and_policy_pipeline(self) -> None:
        proposal = self._fixture()["proposals"][0]
        captured: list[dict[str, object]] = []

        def append(candidates):
            captured.extend(candidates)
            return {
                "ok": True,
                "orders_created": len(candidates),
                "duplicates": 0,
                "order_queue_written": True,
                "order_queue_path": str(self.queue),
                "created_orders": candidates,
            }

        with (
            mock.patch.object(routine_signal_consumer, "update_signal_status", return_value={"ok": True}),
            mock.patch.object(routine_signal_consumer, "routine_execution_intent_admission", return_value={"allowed": True}) as admission,
            mock.patch.object(routine_signal_consumer, "read_order_queue", return_value={"orders": []}),
            mock.patch.object(routine_signal_consumer, "append_order_candidates", side_effect=append),
            mock.patch.object(routine_signal_consumer, "evaluate_order_approval", return_value={"approval_status": "APPROVED", "approval_reason": ""}),
            mock.patch.object(
                routine_signal_consumer.operation_policy_gate,
                "apply_operation_policy_gate_for_order",
                side_effect=lambda order_id, **_kwargs: {
                    "ok": True,
                    "status": "allowed",
                    "after_status": "EXECUTABLE",
                    "policy_status": "EXECUTABLE",
                    "reason": "",
                },
            ),
        ):
            result = routine_signal_consumer.enqueue_final_residual_sell_exit(proposal)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["orders_created"], 1)
        self.assertEqual(result["approved"], 1)
        self.assertEqual(result["policy_executable"], 1)
        self.assertEqual(len(result["executable_order_ids"]), 1)
        admission.assert_called_once()
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["candidate_status"], "CANDIDATE_READY")
        self.assertEqual(captured[0]["hoga"], "MARKET")
        self.assertIsNone(captured[0]["price"])
        self.assertTrue(captured[0]["final_residual_exit"])

    def test_completion_uses_existing_signal_writer(self) -> None:
        proposal = self._fixture(holding=0, available=0, position=0)[
            "completion_proposals"
        ][0]
        with mock.patch.object(
            routine_signal_consumer,
            "update_signal_status",
            return_value={"ok": True},
        ) as writer:
            result = routine_signal_consumer.record_final_residual_sell_exit_completion(
                proposal
            )
        self.assertTrue(result["ok"])
        self.assertEqual(writer.call_args.args[1], "DONE")
        evidence = writer.call_args.kwargs["metadata"]["final_residual_exit_evidence"]
        self.assertTrue(evidence["resulting_holding_zero_confirmed"])

    def test_timer_routes_final_exit_without_stopping_other_work(self) -> None:
        entry = SimpleNamespace(
            stock_code=CODE,
            stock_name="삼성전자",
            stock_dir=Path("unused"),
            execution_ready=True,
            real_trade_enabled=True,
            signal_probe_only=False,
        )
        snapshot = SimpleNamespace(entries=(entry,))
        window = SimpleNamespace(
            current_selected_account_no=lambda: ACCOUNT,
            mark_review_required=mock.Mock(return_value=True),
            statusBarMessage=mock.Mock(),
        )
        empty = {"proposals": [], "reviews": [], "waiting": [], "errors": []}
        reset_empty = {
            **empty,
            "cancel_proposals": [],
            "replan_proposals": [],
            "blocked_execution_process_ids": [],
        }
        repeat_empty = {**empty, "exit_proposals": []}
        final_result = {
            "proposals": [{"execution_process_id": PROCESS}],
            "completion_proposals": [{"execution_process_id": "PROCESS-DONE"}],
            "reviews": [{"code": CODE, "review_reasons": ["FINAL-REVIEW"]}],
            "waiting": [],
            "errors": [],
        }
        consumer = {
            "summary": {
                "signals_checked": 0,
                "blocked": 0,
                "allowed": 0,
                "errors": 0,
                "orders_created": 0,
                "approval_checked": 0,
                "approved": 0,
                "executable_order_ids": [],
            }
        }
        with (
            mock.patch.object(gui_auto_trade_timer, "inspect_sell_price_resets", return_value=reset_empty),
            mock.patch.object(gui_auto_trade_timer, "inspect_unfilled_sell_cancel_eligibility", return_value=empty),
            mock.patch.object(gui_auto_trade_timer, "inspect_due_time_slices", return_value=empty),
            mock.patch.object(gui_auto_trade_timer, "inspect_eligible_ratio_slices", return_value=empty),
            mock.patch.object(gui_auto_trade_timer, "inspect_execution_process_supplements", return_value=empty),
            mock.patch.object(gui_auto_trade_timer, "inspect_sell_repeat_generations", return_value=repeat_empty),
            mock.patch.object(gui_auto_trade_timer, "inspect_sell_final_residual_exits", return_value=final_result),
            mock.patch.object(gui_auto_trade_timer, "enqueue_final_residual_sell_exit", return_value={"ok": True, "orders_created": 1, "executable_order_ids": ["ORDER-FINAL"]}) as enqueue,
            mock.patch.object(gui_auto_trade_timer, "record_final_residual_sell_exit_completion", return_value={"ok": True}) as complete,
            mock.patch.object(gui_auto_trade_timer, "consume_pending_routine_signals_dry_run", return_value=consumer),
            mock.patch.object(gui_auto_trade_timer, "auto_trade_signal_probe_only_active", return_value=True),
            mock.patch.object(gui_auto_trade_timer, "auto_trade_real_execution_active", return_value=False),
            mock.patch.object(gui_auto_trade_timer, "actionable_current_price", return_value=105),
        ):
            result = gui_auto_trade_timer._process_pending_signal_pipeline(window, snapshot)
        self.assertEqual(result["final_residual_exit"]["orders_created"], 1)
        self.assertEqual(result["final_residual_exit"]["completions_recorded"], 1)
        self.assertEqual(result["final_residual_exit"]["reviews"], 1)
        enqueue.assert_called_once()
        complete.assert_called_once()


if __name__ == "__main__":
    unittest.main()
