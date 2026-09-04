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

from execution_buy_exit import evaluate_buy_exit_policy, inspect_buy_repeat_exits
from execution_provenance_contract import materialize_execution_intent_children
import gui_auto_trade_timer
import routine_signal_consumer


ACCOUNT = "81291234"
CODE = "005930"
SIGNAL = "BUY-EXIT-SIGNAL"
PROCESS = "BUY-EXIT-PROCESS"
ROUTINE = "ROUTINE-INSTANCE"
CYCLE = "BUY-CYCLE-1"
OPTION = "OPTION-SNAPSHOT-HASH"


def _policy(*conditions: dict[str, object]) -> dict[str, object]:
    return {"policy": "BUY_REPEAT_EXIT", "enabled": True, "logic": "OR", "conditions": list(conditions)}


class BuyRepeatExitProductionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.queue = root / "order_queue.json"
        self.executions = root / "order_executions.json"
        self.fills = root / "fills.json"
        self.positions = root / "positions.json"
        self.holdings = root / "broker_holdings.json"
        self.signals = root / "routine_signals.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _write(path: Path, **collections: object) -> None:
        path.write_text(json.dumps({"version": 1, **collections}, ensure_ascii=False), encoding="utf-8")

    def _intents(self, *, policy: dict[str, object], buy_round: int = 2,
                 generation: int = 0, repeat_started_at: str = "2026-09-03T10:00:00") -> list[dict[str, object]]:
        intent = {
            "side": "BUY", "execution_mode": "SINGLE_ORDER", "hoga": "LIMIT",
            "price_basis": "ORDER_PRICE", "price": 100, "quantity": 2, "budget": 200,
            "buy_phase": "REPEAT", "buy_round": buy_round,
            "buy_repeat_started_at": repeat_started_at, "plan_generation": generation,
            "source_signal_id": SIGNAL, "execution_process_id": PROCESS,
            "routine_type": "INDICATOR_FOLLOW", "routine_instance_id": ROUTINE,
            "cycle_identity": CYCLE, "option_snapshot_hash": OPTION,
            "buy_exit_policy": deepcopy(policy), "child_sequence_index": 1,
            "child_sequence_total": 1, "child_kind": "SINGLE_ORDER",
            "child_plan": {"planned_quantity": 2, "planned_price": 100},
        }
        return materialize_execution_intent_children(
            [intent], source_signal_id=SIGNAL, execution_process_id=PROCESS,
            plan_generation_value=generation,
        )

    def _fixture(self, *, policy: dict[str, object] | None = None,
                 status: str = "FILLED", remaining: int = 0, filled_quantity: int = 2,
                 buy_round: int = 2, generation: int = 0,
                 repeat_started_at: str = "2026-09-03T10:00:00",
                 cancel: dict[str, object] | None = None,
                 order_updates: dict[str, object] | None = None,
                 signal_updates: dict[str, object] | None = None,
                 runtime_updates: dict[str, object] | None = None,
                 position_updates: dict[str, object] | None = None,
                 holding_updates: dict[str, object] | None = None,
                 fill_updates: dict[str, object] | None = None) -> None:
        policy = policy or _policy({"condition_type": "COUNT", "target_repeat_generations": 1})
        intents = self._intents(policy=policy, buy_round=buy_round, generation=generation,
                                repeat_started_at=repeat_started_at)
        intent = deepcopy(intents[0])
        execution_id = str(intent["execution_id"])
        order = {
            "id": "ORDER-1", "order_id": "ORDER-1", "order_action": "NEW",
            "status": status, "side": "BUY", "account_no": ACCOUNT, "code": CODE,
            "name": "테스트", "broker_order_no": "BROKER-1", "quantity": 2,
            "remaining_quantity": remaining, "source_signal_id": SIGNAL,
            "execution_process_id": PROCESS, "execution_id": execution_id,
            "routine_instance_id": ROUTINE, "cycle_identity": CYCLE,
            "plan_generation": generation, "option_snapshot_hash": OPTION,
            "execution_intent": deepcopy(intent), "created_at": repeat_started_at,
            "updated_at": "2026-09-03T10:01:00",
        }
        order.update(order_updates or {})
        orders = [order] + ([cancel] if cancel is not None else [])
        runtime = {"execution_id": execution_id, "execution_process_id": PROCESS,
                   "plan_generation": generation, "status": status}
        runtime.update(runtime_updates or {})
        fill = {"fill_id": "FILL-1", "execution_id": execution_id,
                "execution_process_id": PROCESS, "filled_quantity": filled_quantity,
                "filled_price": 100, "received_at": "2026-09-03T10:01:00"}
        fill.update(fill_updates or {})
        position = {"account_no": ACCOUNT, "code": CODE, "quantity": filled_quantity,
                    "average_price": 100, "updated_at": "2026-09-03T10:01:00"}
        position.update(position_updates or {})
        holding = {"account_no": ACCOUNT, "code": CODE,
                   "holding_quantity": position["quantity"], "available_quantity": position["quantity"],
                   "received_at": "2026-09-03T10:02:00", "reconciliation_status": "CONSISTENT"}
        holding.update(holding_updates or {})
        signal = {"id": SIGNAL, "code": CODE, "name": "테스트", "signal": "BUY",
                  "routine_instance_id": ROUTINE, "cycle_identity": CYCLE,
                  "execution_process_id": PROCESS, "execution_intent": deepcopy(intent),
                  "execution_intents": deepcopy(intents)}
        signal.update(signal_updates or {})
        self._write(self.queue, orders=orders)
        self._write(self.executions, executions=[runtime],
                    processes=[{"execution_process_id": PROCESS, "option_snapshot_hash": OPTION}])
        self._write(self.fills, fills=[fill] if filled_quantity > 0 else [])
        self._write(self.positions, positions=[position])
        self._write(self.holdings, holdings=[holding])
        self._write(self.signals, signals=[signal])

    def _inspect(self, *, now: str = "2026-09-03T10:03:00", **kwargs: object) -> dict[str, object]:
        return inspect_buy_repeat_exits(
            selected_account_no=ACCOUNT, allowed_stock_codes=[CODE],
            actionable_prices_by_code={CODE: 105}, now=datetime.fromisoformat(now),
            order_queue_path=self.queue, order_executions_path=self.executions,
            fills_path=self.fills, positions_path=self.positions,
            holdings_path=self.holdings, signals_path=self.signals, **kwargs,
        )

    def test_exact_count_boundary_and_success_completion(self) -> None:
        self._fixture()
        result = self._inspect()
        self.assertEqual(1, len(result["completion_proposals"]))
        self.assertEqual([2], result["completion_proposals"][0]["completed_repeat_rounds"])

    def test_completion_writer_preserves_full_cycle_identity(self) -> None:
        self._fixture()
        proposal = self._inspect()["completion_proposals"][0]
        with mock.patch.object(
            routine_signal_consumer,
            "update_signal_status",
            return_value={"ok": True},
        ) as writer:
            result = routine_signal_consumer.record_buy_repeat_exit_completion(proposal)
        self.assertTrue(result["ok"])
        evidence = writer.call_args.kwargs["metadata"]["buy_exit_evidence"]
        self.assertEqual(ROUTINE, evidence["routine_instance_id"])
        self.assertEqual(CYCLE, evidence["cycle_identity"])
        self.assertEqual(SIGNAL, evidence["source_signal_id"])
        self.assertEqual(PROCESS, evidence["execution_process_id"])

    def test_send_uncertain_blocks_completion_and_requests_review(self) -> None:
        self._fixture(status="SEND_UNCERTAIN", order_updates={"manual_reconciliation_required": True})
        result = self._inspect()
        self.assertFalse(result["completion_proposals"])
        self.assertFalse(result["cancel_proposals"])
        self.assertEqual([PROCESS], result["blocked_execution_process_ids"])
        self.assertIn("BUY_EXIT_SEND_UNCERTAIN_OR_RECONCILIATION_REQUIRED",
                      result["reviews"][0]["review_reasons"])

    def test_rejected_and_unknown_children_never_count_or_complete(self) -> None:
        for status in ("BROKER_REJECTED", "SEND_CALL_REJECTED", "FAILED", "UNKNOWN"):
            with self.subTest(status=status):
                self._fixture(status=status)
                result = self._inspect()
                self.assertFalse(result["completion_proposals"])
                self.assertTrue(result["reviews"])

    def test_unfilled_cancelled_repeat_does_not_increment_count(self) -> None:
        self._fixture(status="CANCELLED", filled_quantity=0)
        result = self._inspect()
        self.assertFalse(result["completion_proposals"])
        self.assertFalse(result["cancel_proposals"])

    def test_time_exit_recovers_round_two_anchor_with_generation_zero(self) -> None:
        policy = _policy({"condition_type": "TIME", "configured_value": 1,
                          "configured_unit": "MINUTE", "duration_ms": 60_000})
        self._fixture(policy=policy, status="FILLED", buy_round=2, generation=0,
                      repeat_started_at="2026-09-03T10:00:00")
        self.assertFalse(self._inspect(now="2026-09-03T10:00:59")["completion_proposals"])
        result = self._inspect(now="2026-09-03T10:01:00")
        self.assertEqual(1, len(result["completion_proposals"]))
        self.assertEqual("2026-09-03T10:00:00.000", result["completion_proposals"][0]["repeat_started_at"])

    def test_open_order_is_cancelled_before_completion(self) -> None:
        policy = _policy({"condition_type": "TIME", "configured_value": 1,
                          "configured_unit": "MINUTE", "duration_ms": 60_000})
        self._fixture(policy=policy, status="PARTIALLY_FILLED", remaining=1, filled_quantity=1)
        result = self._inspect()
        self.assertEqual(1, len(result["cancel_proposals"]))
        self.assertEqual(1, result["cancel_proposals"][0]["remaining_quantity"])
        self.assertFalse(result["completion_proposals"])

    def test_active_cancel_and_unconfirmed_effect_block_completion(self) -> None:
        active = {"id": "CANCEL-1", "order_action": "CANCEL", "status": "SEND_CALL_ACCEPTED",
                  "execution_process_id": PROCESS, "original_order_no": "BROKER-1"}
        self._fixture(status="CANCELLED", cancel=active)
        result = self._inspect()
        self.assertFalse(result["completion_proposals"])
        self.assertEqual("BUY_EXIT_ACTIVE_CANCEL", result["waiting"][0]["reason"])
        completed = dict(active)
        completed.update(status="CANCELLED", original_order_effect_confirmed=False)
        self._fixture(status="CANCELLED", cancel=completed)
        result = self._inspect()
        self.assertFalse(result["completion_proposals"])
        self.assertEqual("BUY_EXIT_CANCEL_EFFECT_PENDING", result["waiting"][0]["reason"])

    def test_confirmed_cancel_effect_allows_completion_after_partial_fill(self) -> None:
        cancel = {"id": "CANCEL-1", "order_action": "CANCEL", "status": "CANCELLED",
                  "execution_process_id": PROCESS, "original_order_no": "BROKER-1",
                  "original_order_effect_confirmed": True}
        self._fixture(status="CANCELLED", filled_quantity=1, cancel=cancel)
        self.assertEqual(1, len(self._inspect()["completion_proposals"]))

    def test_identity_and_authoritative_mismatches_block_completion(self) -> None:
        cases = (
            ("source signal", {"order_updates": {"source_signal_id": "OTHER"}}),
            ("routine instance", {"order_updates": {"routine_instance_id": "OTHER"}}),
            ("cycle", {"order_updates": {"cycle_identity": "OTHER"}}),
            ("runtime", {"runtime_updates": {"execution_process_id": "OTHER"}}),
            ("fill", {"fill_updates": {"execution_process_id": "OTHER"}}),
            ("position holding", {"holding_updates": {"holding_quantity": 99}}),
            ("stale holding", {"holding_updates": {"received_at": "2026-09-03T09:59:00"}}),
        )
        for label, values in cases:
            with self.subTest(case=label):
                self._fixture(**values)
                result = self._inspect()
                self.assertFalse(result["completion_proposals"])
                self.assertEqual([PROCESS], result["blocked_execution_process_ids"])
                self.assertTrue(result["reviews"])

    def test_existing_exit_evidence_must_match_full_cycle_identity(self) -> None:
        evidence = {"buy_phase_completed": True, "execution_process_id": PROCESS,
                    "source_signal_id": SIGNAL, "routine_instance_id": ROUTINE,
                    "cycle_identity": "OLD-CYCLE"}
        self._fixture(signal_updates={"buy_exit_evidence": evidence})
        result = self._inspect()
        self.assertFalse(result["completion_proposals"])
        self.assertIn("BUY_EXIT_EVIDENCE_IDENTITY_INVALID", result["reviews"][0]["review_reasons"])

    def test_external_priority_block_prevents_any_exit_action(self) -> None:
        self._fixture()
        result = self._inspect(blocked_execution_process_ids=[PROCESS])
        self.assertFalse(result["completion_proposals"])
        self.assertFalse(result["cancel_proposals"])

    def test_authoritative_ledger_read_failure_still_blocks_known_process(self) -> None:
        self._fixture()
        self.holdings.write_text("{}", encoding="utf-8")
        result = self._inspect()
        self.assertFalse(result["ok"])
        self.assertEqual([PROCESS], result["blocked_execution_process_ids"])
        self.assertFalse(result["completion_proposals"])


class BuyRepeatExitPolicyUnitTest(unittest.TestCase):
    def test_stale_price_does_not_hide_matched_count_under_or(self) -> None:
        result = evaluate_buy_exit_policy(
            policy=_policy(
                {"condition_type": "COUNT", "target_repeat_generations": 1},
                {"condition_type": "PRICE", "left_source": "ORDER_PRICE",
                 "right_source": "CURRENT_PRICE", "direction": "UP", "compare": ">=",
                 "threshold_percent": 1},
            ),
            completed_repeat_count=1, repeat_started_at=None, order_price=100,
            current_price=None, average_price=None, now=datetime(2026, 9, 3, 10, 0),
        )
        self.assertTrue(result["triggered"])
        self.assertIn("BUY_REPEAT_EXIT_CURRENT_PRICE_UNAVAILABLE", result["waiting_reasons"])

    def test_operation_cycle_runs_exit_before_reset_and_passes_process_block(self) -> None:
        call_order: list[str] = []
        exit_result = {
            "ok": True,
            "cancel_proposals": [],
            "completion_proposals": [{"source_signal_id": SIGNAL}],
            "reviews": [], "waiting": [], "errors": [],
            "blocked_execution_process_ids": [PROCESS],
        }

        def inspect_exit(**_kwargs: object) -> dict[str, object]:
            call_order.append("exit")
            return exit_result

        def inspect_reset(**kwargs: object) -> dict[str, object]:
            call_order.append("reset")
            self.assertEqual((PROCESS,), kwargs["blocked_execution_process_ids"])
            return {"cancel_proposals": [], "replan_proposals": [], "reviews": [],
                    "waiting": [], "errors": [], "blocked_execution_process_ids": [PROCESS]}

        empty = {"proposals": [], "cancel_proposals": [], "completion_proposals": [],
                 "replan_proposals": [], "reviews": [], "waiting": [], "errors": [],
                 "blocked_execution_process_ids": []}
        entry = SimpleNamespace(stock_code=CODE, stock_name="테스트", stock_dir=Path("unused"),
                                execution_ready=True, signal_probe_only=False)
        snapshot = SimpleNamespace(entries=(entry,))
        window = SimpleNamespace(
            current_selected_account_no=lambda: ACCOUNT,
            current_orderable_cash_for_budget=lambda: 1_000_000,
            mark_review_required=mock.Mock(return_value=True), statusBarMessage=mock.Mock(),
        )
        consumer = {"summary": {"signals_checked": 0, "blocked": 0, "allowed": 0,
                                "errors": 0, "orders_created": 0, "approval_checked": 0,
                                "approved": 0, "executable_order_ids": []}}
        with (
            mock.patch.object(gui_auto_trade_timer, "inspect_buy_repeat_exits", side_effect=inspect_exit),
            mock.patch.object(gui_auto_trade_timer, "record_buy_repeat_exit_completion",
                              return_value={"ok": True}),
            mock.patch.object(gui_auto_trade_timer, "inspect_buy_price_resets", side_effect=inspect_reset),
            mock.patch.object(gui_auto_trade_timer, "inspect_sell_price_resets", return_value=empty),
            mock.patch.object(gui_auto_trade_timer, "inspect_unfilled_sell_cancel_eligibility", return_value=empty),
            mock.patch.object(gui_auto_trade_timer, "inspect_due_time_slices", return_value=empty),
            mock.patch.object(gui_auto_trade_timer, "inspect_eligible_ratio_slices", return_value=empty),
            mock.patch.object(gui_auto_trade_timer, "inspect_execution_process_supplements", return_value=empty),
            mock.patch.object(gui_auto_trade_timer, "inspect_sell_repeat_generations", return_value=empty),
            mock.patch.object(gui_auto_trade_timer, "inspect_sell_final_residual_exits", return_value=empty),
            mock.patch.object(gui_auto_trade_timer, "consume_pending_routine_signals_dry_run", return_value=consumer),
            mock.patch.object(gui_auto_trade_timer, "auto_trade_signal_probe_only_active", return_value=True),
            mock.patch.object(gui_auto_trade_timer, "auto_trade_real_execution_active", return_value=False),
            mock.patch.object(gui_auto_trade_timer, "actionable_current_price", return_value=105),
        ):
            result = gui_auto_trade_timer._process_pending_signal_pipeline(window, snapshot)

        self.assertEqual(["exit", "reset"], call_order)
        self.assertEqual(1, result["buy_exit"]["completion_proposals"])
        self.assertEqual(0, result["price_reset"]["replan_proposals"])
        self.assertEqual(0, result["price_reset"]["orders_created"])


if __name__ == "__main__":
    unittest.main()
