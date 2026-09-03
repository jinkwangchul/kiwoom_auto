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

from execution_price_comparison import evaluate_percent_comparison
from execution_price_reset import inspect_sell_price_resets
from execution_provenance_contract import materialize_execution_intent_children
import routine_signal_consumer
import gui_auto_trade_timer


ACCOUNT = "81291234"
CODE = "005930"
SIGNAL = "SIGNAL-RESET-1"
PROCESS = "PROCESS-RESET-1"


class ExecutionPriceResetTest(unittest.TestCase):
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
    def _policy() -> dict[str, object]:
        return {
            "policy": "SELL_PRICE_CHANGE_RESET",
            "action": "RESET",
            "left_source": "ORDER_PRICE",
            "right_source": "CURRENT_PRICE",
            "direction": "UP",
            "compare": ">=",
            "threshold_percent": 5,
            "order_price": 100,
        }

    def _intents(self, mode: str = "SINGLE_ORDER", *, quantity: int = 10) -> list[dict[str, object]]:
        common: dict[str, object] = {
            "side": "SELL",
            "budget": None,
            "routine_type": "INDICATOR_FOLLOW",
            "routine_instance_id": "INSTANCE-1",
            "source_signal_id": SIGNAL,
            "execution_process_id": PROCESS,
            "option_snapshot_hash": "OPTION-HASH",
            "plan_generation": 0,
            "planned_total_quantity": quantity,
            "sell_price_reset_policy": self._policy(),
        }
        if mode == "MULTI_HOGA":
            offsets = [0, 1, -1]
            quantities = [4, 3, 3]
            values = []
            for index, (offset, child_quantity) in enumerate(zip(offsets, quantities), start=1):
                values.append({
                    **deepcopy(common), "execution_mode": mode, "quantity": child_quantity,
                    "price": 100, "hoga": "LIMIT", "price_basis": "ORDER_PRICE",
                    "child_sequence_index": index, "child_sequence_total": 3,
                    "child_kind": "HOGA_LEVEL",
                    "child_plan": {"planned_quantity": child_quantity, "planned_price": 100, "hoga_offset_ticks": offset},
                    "multi_hoga_plan": {"base_price": 100, "hoga_offsets": offsets, "planned_child_count": 3, "planned_total_quantity": quantity, "instrument_type": "STOCK"},
                })
        elif mode == "MULTI_TIME":
            values = []
            for index, child_quantity in enumerate([4, 3, 3], start=1):
                values.append({
                    **deepcopy(common), "execution_mode": mode, "quantity": child_quantity,
                    "price": 100, "hoga": "LIMIT", "price_basis": "ORDER_PRICE",
                    "child_sequence_index": index, "child_sequence_total": 3,
                    "child_kind": "TIME_SLICE",
                    "child_plan": {"planned_quantity": child_quantity, "planned_price": 100, "scheduled_offset_ms": (index - 1) * 1_000},
                    "multi_time_plan": {"configured_child_count": 3, "planned_child_count": 3, "planned_total_quantity": quantity, "scheduled_offsets_ms": [0, 1_000, 2_000], "price_basis": "ORDER_PRICE"},
                })
        elif mode == "MULTI_RATIO":
            values = []
            plan = {"configured_child_count": 3, "planned_child_count": 3, "planned_total_quantity": quantity, "ratio_left": "ORDER_PRICE", "ratio_right": "CURRENT_PRICE", "ratio_direction": "UP", "ratio_compare": ">=", "ratio_value": 1, "order_price": 100}
            for index, child_quantity in enumerate([4, 3, 3], start=1):
                values.append({
                    **deepcopy(common), "execution_mode": mode, "quantity": child_quantity,
                    "price": 100, "hoga": "LIMIT", "price_basis": "ORDER_PRICE",
                    "child_sequence_index": index, "child_sequence_total": 3,
                    "child_kind": "RATIO_SLICE",
                    "child_plan": {"planned_quantity": child_quantity, "planned_price": 100, "ratio_step_index": index},
                    "multi_ratio_plan": deepcopy(plan),
                })
        else:
            values = [{
                **common, "execution_mode": "SINGLE_ORDER", "quantity": quantity,
                "price": 100, "hoga": "LIMIT", "price_basis": "ORDER_PRICE",
                "child_sequence_index": 1, "child_sequence_total": 1,
                "child_kind": "SINGLE_ORDER",
                "child_plan": {"planned_quantity": quantity, "planned_price": 100},
            }]
        return materialize_execution_intent_children(
            values,
            source_signal_id=SIGNAL,
            execution_process_id=PROCESS,
            plan_generation_value=0,
        )

    def _fixture(
        self,
        *,
        mode: str = "SINGLE_ORDER",
        status: str = "CANCELLED",
        remaining: int = 0,
        holding: int = 10,
        available: int | None = None,
        current_price: int | None = 105,
        extra_orders: list[dict[str, object]] | None = None,
        runtime: bool = True,
        position_quantity: int | None = None,
        holding_received_at: str = "2026-09-03T10:02:00",
    ) -> dict[str, object]:
        intents = self._intents(mode)
        first = intents[0]
        order = {
            "id": "ORDER-ORIGINAL-1", "order_id": "ORIGINAL-1",
            "status": status, "source_signal_id": SIGNAL,
            "execution_process_id": PROCESS, "execution_id": first["execution_id"],
            "plan_generation": 0, "option_snapshot_hash": "OPTION-HASH",
            "account_no": ACCOUNT, "code": CODE, "name": "삼성전자",
            "side": "SELL", "order_action": "NEW", "broker_order_no": "BROKER-1",
            "remaining_quantity": remaining, "quantity": first["quantity"],
            "execution_intent": deepcopy(first), "updated_at": "2026-09-03T10:00:00",
        }
        orders = [order] + list(extra_orders or [])
        self._write(self.queue, orders=orders)
        self._write(
            self.executions,
            executions=[{
                "execution_id": first["execution_id"],
                "execution_process_id": PROCESS,
                "plan_generation": 0,
            }] if runtime else [],
            processes=[{"execution_process_id": PROCESS, "option_snapshot_hash": "OPTION-HASH"}],
        )
        self._write(self.fills, fills=[])
        self._write(self.positions, positions=[{
            "account_no": ACCOUNT, "code": CODE,
            "quantity": holding if position_quantity is None else position_quantity,
            "average_price": 95, "updated_at": "2026-09-03T10:01:00",
        }])
        self._write(self.holdings, holdings=[{
            "account_no": ACCOUNT, "code": CODE,
            "holding_quantity": holding,
            "available_quantity": holding if available is None else available,
            "received_at": holding_received_at,
            "reconciliation_status": "CONSISTENT",
            "manual_reconciliation_required": False,
        }])
        self._write(self.signals, signals=[{
            "id": SIGNAL, "routine": "지표추종매매", "routine_instance_id": "INSTANCE-1",
            "code": CODE, "name": "삼성전자", "signal": "SELL", "status": "PENDING",
            "execution_intent": deepcopy(first), "execution_intents": deepcopy(intents),
        }])
        return self._inspect(current_price=current_price)

    def _inspect(self, *, current_price: int | None = 105) -> dict[str, object]:
        return inspect_sell_price_resets(
            selected_account_no=ACCOUNT,
            allowed_stock_codes=(CODE,),
            actionable_prices_by_code={CODE: current_price},
            now=datetime.fromisoformat("2026-09-03T10:03:00"),
            order_queue_path=self.queue,
            order_executions_path=self.executions,
            fills_path=self.fills,
            positions_path=self.positions,
            holdings_path=self.holdings,
            signals_path=self.signals,
        )

    def test_current_cycle_repeat_exit_block_prevents_sell_reset(self) -> None:
        self._fixture()
        result = inspect_sell_price_resets(
            selected_account_no=ACCOUNT,
            allowed_stock_codes=(CODE,),
            actionable_prices_by_code={CODE: 105},
            blocked_execution_process_ids=(PROCESS,),
            now=datetime.fromisoformat("2026-09-03T10:03:00"),
            order_queue_path=self.queue,
            order_executions_path=self.executions,
            fills_path=self.fills,
            positions_path=self.positions,
            holdings_path=self.holdings,
            signals_path=self.signals,
        )

        self.assertEqual([], result["cancel_proposals"])
        self.assertEqual([], result["replan_proposals"])
        self.assertEqual([PROCESS], result["blocked_execution_process_ids"])
        self.assertEqual(
            "SELL_PRICE_RESET_REPEAT_EXIT_PRECEDENCE",
            result["waiting"][0]["reason"],
        )

    def test_durable_repeat_exit_blocks_price_reset_replan(self) -> None:
        self._fixture()
        data = json.loads(self.signals.read_text(encoding="utf-8"))
        data["signals"][0]["sell_repeat_exit_evidence"] = {
            "execution_process_id": PROCESS,
            "source_signal_id": SIGNAL,
            "exit_source_snapshot_hash": "EXIT-HASH",
        }
        self.signals.write_text(json.dumps(data), encoding="utf-8")

        result = self._inspect()

        self.assertEqual([], result["cancel_proposals"])
        self.assertEqual([], result["replan_proposals"])
        self.assertEqual(
            "SELL_PRICE_RESET_REPEAT_EXITED",
            result["waiting"][0]["reason"],
        )

    def test_shared_threshold_semantics_cover_exact_and_not_met(self) -> None:
        exact, observed = evaluate_percent_comparison(left=100, right=105, direction="UP", compare=">=", threshold=5)
        outside, absolute = evaluate_percent_comparison(left=100, right=94, direction="BOTH", compare="OUTSIDE", threshold=5)
        self.assertTrue(exact)
        self.assertAlmostEqual(5, observed)
        self.assertTrue(outside)
        self.assertAlmostEqual(6, absolute)
        result = self._fixture(current_price=104)
        self.assertEqual([], result["cancel_proposals"])
        self.assertEqual([], result["replan_proposals"])
        self.assertEqual("SELL_PRICE_RESET_THRESHOLD_NOT_MET", result["waiting"][0]["reason"])

    def test_open_order_requires_cancel_first_and_uses_latest_remaining(self) -> None:
        result = self._fixture(status="PARTIALLY_FILLED", remaining=6, holding=6)
        self.assertEqual([], result["replan_proposals"])
        self.assertEqual(1, len(result["cancel_proposals"]))
        self.assertEqual(6, result["cancel_proposals"][0]["remaining_quantity"])
        self.assertIn(PROCESS, result["blocked_execution_process_ids"])

    def test_active_cancel_waits_and_uncertain_is_reconciled(self) -> None:
        active = {
            "id": "CANCEL-1", "status": "ORDER_QUEUED", "order_action": "CANCEL",
            "execution_process_id": PROCESS, "account_no": ACCOUNT, "code": CODE, "side": "SELL",
            "execution_request": {"request_preview": {"order_action": "CANCEL", "original_order_no": "BROKER-1"}},
        }
        waiting = self._fixture(status="BROKER_ACCEPTED", remaining=10, extra_orders=[active])
        self.assertEqual([], waiting["cancel_proposals"])
        self.assertEqual("SELL_PRICE_RESET_CANCEL_REQUIRED", waiting["waiting"][-1]["reason"])
        active["status"] = "SEND_UNCERTAIN"
        active["manual_reconciliation_required"] = True
        reviewed = self._fixture(status="BROKER_ACCEPTED", remaining=10, extra_orders=[active])
        self.assertIn("SELL_PRICE_RESET_CANCEL_SEND_UNCERTAIN", " ".join(reviewed["reviews"][0]["review_reasons"]))

    def test_cancel_effect_confirmation_precedes_latest_holding_replan(self) -> None:
        cancel = {
            "id": "CANCEL-1", "status": "CANCELLED", "order_action": "CANCEL",
            "execution_process_id": PROCESS, "account_no": ACCOUNT, "code": CODE, "side": "SELL",
            "updated_at": "2026-09-03T10:00:30",
            "cancel_evidence": {"trigger": "SELL_PRICE_CHANGE_RESET", "source_plan_generation": 0, "trigger_snapshot_hash": "RESET-SNAPSHOT-1"},
            "execution_request": {"request_preview": {"order_action": "CANCEL", "original_order_no": "BROKER-1"}},
        }
        result = self._fixture(status="PARTIAL_CANCELLED", remaining=0, holding=6, extra_orders=[cancel])
        proposal = result["replan_proposals"][0]
        self.assertEqual(6, proposal["latest_sellable_quantity"])
        self.assertEqual(1, proposal["plan_generation"])
        self.assertEqual(SIGNAL, proposal["execution_intents"][0]["source_signal_id"])
        self.assertEqual(PROCESS, proposal["execution_intents"][0]["execution_process_id"])
        self.assertNotEqual(self._intents()[0]["execution_id"], proposal["execution_intents"][0]["execution_id"])

    def test_cancel_effect_pending_and_holding_staleness_do_not_replan(self) -> None:
        cancel = {
            "id": "CANCEL-1", "status": "BROKER_ACCEPTED", "order_action": "CANCEL",
            "execution_process_id": PROCESS,
            "cancel_evidence": {"trigger": "SELL_PRICE_CHANGE_RESET", "source_plan_generation": 0, "trigger_snapshot_hash": "RESET-SNAPSHOT-1"},
            "execution_request": {"request_preview": {"order_action": "CANCEL", "original_order_no": "BROKER-1"}},
        }
        pending = self._fixture(status="BROKER_ACCEPTED", remaining=6, extra_orders=[cancel])
        self.assertEqual([], pending["replan_proposals"])
        stale = self._fixture(holding_received_at="2026-09-03T09:59:00")
        self.assertEqual([], stale["replan_proposals"])
        self.assertEqual("SELL_PRICE_RESET_HOLDING_EVIDENCE_PENDING", stale["waiting"][-1]["reason"])

    def test_terminal_cancel_without_original_effect_is_not_blindly_retried(self) -> None:
        cancel = {
            "id": "CANCEL-1", "status": "CANCELLED", "order_action": "CANCEL",
            "execution_process_id": PROCESS,
            "cancel_evidence": {"trigger": "SELL_PRICE_CHANGE_RESET", "source_plan_generation": 0, "trigger_snapshot_hash": "RESET-SNAPSHOT-1"},
            "execution_request": {"request_preview": {"order_action": "CANCEL", "original_order_no": "BROKER-1"}},
        }
        result = self._fixture(status="BROKER_ACCEPTED", remaining=6, extra_orders=[cancel])
        self.assertEqual([], result["cancel_proposals"])
        self.assertEqual([], result["replan_proposals"])
        self.assertIn("SELL_PRICE_RESET_CANCEL_EFFECT_UNCONFIRMED", " ".join(result["reviews"][0]["review_reasons"]))

    def test_position_broker_mismatch_and_runtime_gap_are_isolated(self) -> None:
        mismatch = self._fixture(holding=6, position_quantity=7)
        self.assertIn("SELL_PRICE_RESET_POSITION_BROKER_MISMATCH", " ".join(mismatch["reviews"][0]["review_reasons"]))
        missing_runtime = self._fixture(runtime=False)
        self.assertIn("SELL_PRICE_RESET_RUNTIME_EXECUTION_MISSING", " ".join(missing_runtime["reviews"][0]["review_reasons"]))

    def test_single_hoga_time_and_ratio_replans_use_generation_one(self) -> None:
        for mode, kind in (
            ("SINGLE_ORDER", "SINGLE_ORDER"),
            ("MULTI_HOGA", "HOGA_LEVEL"),
            ("MULTI_TIME", "TIME_SLICE"),
            ("MULTI_RATIO", "RATIO_SLICE"),
        ):
            with self.subTest(mode=mode):
                result = self._fixture(mode=mode)
                intents = result["replan_proposals"][0]["execution_intents"]
                self.assertEqual({1}, {item["plan_generation"] for item in intents})
                self.assertEqual({kind}, {item["child_kind"] for item in intents})
                self.assertEqual(10, sum(item["quantity"] for item in intents))

    def test_same_snapshot_is_not_generated_twice_and_recovery_keeps_generation(self) -> None:
        first = self._fixture()
        proposal = first["replan_proposals"][0]
        signal_data = json.loads(self.signals.read_text(encoding="utf-8"))
        signal_data["signals"][0]["execution_intent"] = proposal["execution_intents"][0]
        signal_data["signals"][0]["execution_intents"] = proposal["execution_intents"]
        self.signals.write_text(json.dumps(signal_data), encoding="utf-8")
        replay = self._inspect()
        self.assertEqual([], replay["replan_proposals"])
        self.assertEqual("SELL_PRICE_RESET_GENERATION_PENDING_EXECUTION", replay["waiting"][-1]["reason"])
        self.assertNotIn(PROCESS, replay["blocked_execution_process_ids"])

    def test_enqueue_persists_deferred_plan_and_immediate_plan_uses_generic_pipeline(self) -> None:
        time_proposal = self._fixture(mode="MULTI_TIME")["replan_proposals"][0]
        with mock.patch.object(routine_signal_consumer, "update_signal_status", return_value={"ok": True}) as update:
            deferred = routine_signal_consumer.enqueue_price_reset_generation(time_proposal)
        self.assertTrue(deferred["ok"])
        self.assertTrue(deferred["deferred"])
        self.assertEqual("PENDING", update.call_args.args[1])

        single_proposal = self._fixture()["replan_proposals"][0]
        with (
            mock.patch.object(routine_signal_consumer, "update_signal_status", return_value={"ok": True}) as update,
            mock.patch.object(routine_signal_consumer, "enqueue_replanned_execution_intents", return_value={"ok": True, "orders_created": 1, "executable_order_ids": ["ORDER-NEW"]}) as enqueue,
        ):
            immediate = routine_signal_consumer.enqueue_price_reset_generation(single_proposal)
        self.assertTrue(immediate["ok"])
        self.assertEqual(1, immediate["orders_created"])
        enqueue.assert_called_once()
        self.assertEqual(2, update.call_count)

    def test_operation_cycle_routes_cancel_and_replan_without_stopping_other_processes(self) -> None:
        entry = SimpleNamespace(
            stock_code=CODE,
            stock_name="삼성전자",
            stock_dir=Path("unused"),
            execution_ready=True,
            real_trade_enabled=True,
            signal_probe_only=False,
        )
        snapshot = SimpleNamespace(entries=(entry,))
        requester = mock.Mock(return_value={"ok": True, "cancel_requested": 1, "cancel_pending": 0})
        window = SimpleNamespace(
            current_selected_account_no=lambda: ACCOUNT,
            queue_open_order_cancel_automatically=requester,
            mark_review_required=mock.Mock(return_value=True),
            statusBarMessage=mock.Mock(),
        )
        inspected = {
            "cancel_proposals": [{
                "order_queued_id": "ORDER-1", "account_no": ACCOUNT,
                "code": CODE, "side": "SELL", "broker_order_no": "BROKER-1",
                "remaining_quantity": 5, "source_plan_generation": 0,
                "trigger_snapshot": {"snapshot_hash": "RESET-HASH"},
            }],
            "replan_proposals": [{"signal": {"id": SIGNAL}, "execution_intents": [{}]}],
            "reviews": [{"code": CODE, "review_reasons": ["RESET-REVIEW"]}],
            "waiting": [], "errors": [],
            "blocked_execution_process_ids": [PROCESS],
        }
        empty = {"proposals": [], "reviews": [], "waiting": [], "errors": []}
        exit_empty = {
            "ok": True,
            "proposals": [],
            "exit_proposals": [],
            "blocked_execution_process_ids": [],
            "reviews": [],
            "waiting": [],
            "errors": [],
        }
        consumer = {"summary": {"signals_checked": 0, "blocked": 0, "allowed": 0, "errors": 0, "orders_created": 0, "approval_checked": 0, "approved": 0, "executable_order_ids": []}}
        with (
            mock.patch.object(gui_auto_trade_timer, "inspect_sell_repeat_exits", return_value=exit_empty),
            mock.patch.object(gui_auto_trade_timer, "inspect_sell_price_resets", return_value=inspected) as reset_inspect,
            mock.patch.object(gui_auto_trade_timer, "enqueue_price_reset_generation", return_value={"ok": True, "orders_created": 1, "executable_order_ids": ["ORDER-RESET-1"]}),
            mock.patch.object(gui_auto_trade_timer, "inspect_unfilled_sell_cancel_eligibility", return_value=empty),
            mock.patch.object(gui_auto_trade_timer, "inspect_due_time_slices", return_value=empty) as time_inspect,
            mock.patch.object(gui_auto_trade_timer, "inspect_eligible_ratio_slices", return_value=empty) as ratio_inspect,
            mock.patch.object(gui_auto_trade_timer, "inspect_execution_process_supplements", return_value=empty) as supplement_inspect,
            mock.patch.object(gui_auto_trade_timer, "consume_pending_routine_signals_dry_run", return_value=consumer),
            mock.patch.object(gui_auto_trade_timer, "auto_trade_signal_probe_only_active", return_value=True),
            mock.patch.object(gui_auto_trade_timer, "auto_trade_real_execution_active", return_value=False),
            mock.patch.object(gui_auto_trade_timer, "actionable_current_price", return_value=105),
        ):
            result = gui_auto_trade_timer._process_pending_signal_pipeline(window, snapshot)

        self.assertEqual(1, result["price_reset"]["cancel_requested"])
        self.assertEqual(1, result["price_reset"]["orders_created"])
        self.assertEqual(1, result["price_reset"]["reviews"])
        requester.assert_called_once()
        evidence = requester.call_args.kwargs["cancel_evidence"]
        self.assertEqual("SELL_PRICE_CHANGE_RESET", evidence["trigger"])
        self.assertEqual("RESET-HASH", evidence["trigger_snapshot_hash"])
        self.assertEqual(
            (),
            reset_inspect.call_args.kwargs[
                "blocked_execution_process_ids"
            ],
        )
        self.assertEqual((PROCESS,), time_inspect.call_args.kwargs["blocked_execution_process_ids"])
        self.assertEqual((PROCESS,), ratio_inspect.call_args.kwargs["blocked_execution_process_ids"])
        self.assertEqual((PROCESS,), supplement_inspect.call_args.kwargs["blocked_execution_process_ids"])


if __name__ == "__main__":
    unittest.main()
