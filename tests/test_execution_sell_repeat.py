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
from execution_price_reset import inspect_sell_price_resets
from execution_sell_repeat import inspect_sell_repeat_exits, inspect_sell_repeat_generations
import gui_auto_trade_timer
import routine_signal_consumer


ACCOUNT = "81291234"
CODE = "005930"
SIGNAL = "SIGNAL-REPEAT-1"
PROCESS = "PROCESS-REPEAT-1"


class ExecutionSellRepeatTest(unittest.TestCase):
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
    def _repeat_policy(mode: str) -> dict[str, object]:
        template: dict[str, object]
        if mode == "MULTI_HOGA":
            template = {
                "execution_mode": mode,
                "hoga": "LIMIT",
                "price_basis": "ORDER_PRICE",
                "hoga_offsets": [0, 1, -1],
                "instrument_type": "STOCK",
            }
        elif mode == "MULTI_TIME":
            template = {
                "execution_mode": mode,
                "hoga": "LIMIT",
                "price_basis": "ORDER_PRICE",
                "configured_child_count": 3,
                "time_value": 1,
                "time_unit_milliseconds": 1_000,
                "time_range": "INTERVAL",
            }
        elif mode == "MULTI_RATIO":
            template = {
                "execution_mode": mode,
                "hoga": "LIMIT",
                "price_basis": "ORDER_PRICE",
                "configured_child_count": 3,
                "ratio_left": "ORDER_PRICE",
                "ratio_right": "CURRENT_PRICE",
                "ratio_direction": "UP",
                "ratio_value": 1,
                "ratio_compare": ">=",
            }
        else:
            template = {
                "execution_mode": "SINGLE_ORDER",
                "hoga": "LIMIT",
                "price_basis": "ORDER_PRICE",
            }
        policy = {
            "policy": "SELL_FOLLOW_UP_REPEAT",
            "enabled": True,
            "execution_template": template,
            "unfilled_timeout_policy": {
                "policy": "SELL_UNFILLED_TIMEOUT_CANCEL",
                "scope": "EACH",
                "timeout_ms": 20_000,
            },
            "sell_price_reset_policy": {
                "policy": "SELL_PRICE_CHANGE_RESET",
                "action": "RESET",
                "left_source": "ORDER_PRICE",
                "right_source": "CURRENT_PRICE",
                "direction": "UP",
                "compare": ">=",
                "threshold_percent": 5,
                "order_price": None,
            },
            "exit_policy_snapshot": {"exit_price_check": True},
            "exit_policy": {
                "policy": "SELL_REPEAT_EXIT",
                "logic": "OR",
                "conditions": [],
                "snapshot_hash": "EXIT-POLICY-HASH",
            },
        }
        policy["plan_snapshot_hash"] = "REPEAT-PLAN-HASH"
        return policy

    def _intents(self, mode: str = "SINGLE_ORDER", quantity: int = 10) -> list[dict[str, object]]:
        policy = self._repeat_policy(mode)
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
            "sell_repeat_policy": policy,
        }
        if mode == "MULTI_HOGA":
            values = [
                {
                    **deepcopy(common),
                    "execution_mode": mode,
                    "quantity": child_quantity,
                    "price": 100,
                    "hoga": "LIMIT",
                    "child_sequence_index": index,
                    "child_sequence_total": 3,
                    "child_kind": "HOGA_LEVEL",
                    "child_plan": {"planned_quantity": child_quantity, "hoga_offset_ticks": offset},
                    "multi_hoga_plan": {
                        "base_price": 100,
                        "hoga_offsets": [0, 1, -1],
                        "planned_child_count": 3,
                        "planned_total_quantity": quantity,
                        "instrument_type": "STOCK",
                    },
                }
                for index, (offset, child_quantity) in enumerate(zip([0, 1, -1], [4, 3, 3]), start=1)
            ]
        elif mode == "MULTI_TIME":
            values = [
                {
                    **deepcopy(common),
                    "execution_mode": mode,
                    "quantity": child_quantity,
                    "price": 100,
                    "hoga": "LIMIT",
                    "child_sequence_index": index,
                    "child_sequence_total": 3,
                    "child_kind": "TIME_SLICE",
                    "child_plan": {"planned_quantity": child_quantity, "scheduled_offset_ms": (index - 1) * 1_000},
                    "multi_time_plan": {
                        "configured_child_count": 3,
                        "planned_child_count": 3,
                        "planned_total_quantity": quantity,
                        "scheduled_offsets_ms": [0, 1_000, 2_000],
                    },
                }
                for index, child_quantity in enumerate([4, 3, 3], start=1)
            ]
        elif mode == "MULTI_RATIO":
            ratio_plan = {
                "configured_child_count": 3,
                "planned_child_count": 3,
                "planned_total_quantity": quantity,
                "ratio_left": "ORDER_PRICE",
                "ratio_right": "CURRENT_PRICE",
                "ratio_direction": "UP",
                "ratio_value": 1,
                "ratio_compare": ">=",
                "order_price": 100,
            }
            values = [
                {
                    **deepcopy(common),
                    "execution_mode": mode,
                    "quantity": child_quantity,
                    "price": 100,
                    "hoga": "LIMIT",
                    "child_sequence_index": index,
                    "child_sequence_total": 3,
                    "child_kind": "RATIO_SLICE",
                    "child_plan": {"planned_quantity": child_quantity, "ratio_step_index": index},
                    "multi_ratio_plan": deepcopy(ratio_plan),
                }
                for index, child_quantity in enumerate([4, 3, 3], start=1)
            ]
        else:
            values = [{
                **common,
                "execution_mode": "SINGLE_ORDER",
                "quantity": quantity,
                "price": 100,
                "hoga": "LIMIT",
                "child_sequence_index": 1,
                "child_sequence_total": 1,
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
        status: str = "FILLED",
        holding: int = 3,
        available: int | None = None,
        position: int | None = None,
        current_price: int | None = 105,
        holding_received_at: str = "2026-09-03T10:02:00",
        extra_orders: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        intents = self._intents(mode)
        orders: list[dict[str, object]] = []
        executions: list[dict[str, object]] = []
        for index, intent in enumerate(intents, start=1):
            orders.append({
                "id": f"QUEUE-{index}",
                "order_id": f"ORDER-{index}",
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
                "broker_order_no": f"BROKER-{index}",
                "remaining_quantity": 0 if status not in {"BROKER_ACCEPTED", "PARTIALLY_FILLED"} else int(intent["quantity"]),
                "quantity": intent["quantity"],
                "execution_intent": deepcopy(intent),
                "updated_at": "2026-09-03T10:00:00",
            })
            executions.append({
                "execution_id": intent["execution_id"],
                "execution_process_id": PROCESS,
                "plan_generation": 0,
            })
        orders.extend(extra_orders or [])
        self._write(self.queue, orders=orders)
        self._write(
            self.executions,
            executions=executions,
            processes=[{"execution_process_id": PROCESS, "option_snapshot_hash": "OPTION-HASH"}],
        )
        self._write(self.fills, fills=[])
        self._write(self.positions, positions=[{
            "account_no": ACCOUNT,
            "code": CODE,
            "quantity": holding if position is None else position,
            "average_price": 95,
            "updated_at": "2026-09-03T10:01:00",
        }])
        self._write(self.holdings, holdings=[{
            "account_no": ACCOUNT,
            "code": CODE,
            "holding_quantity": holding,
            "available_quantity": holding if available is None else available,
            "received_at": holding_received_at,
            "reconciliation_status": "CONSISTENT",
            "manual_reconciliation_required": False,
        }])
        self._write(self.signals, signals=[{
            "id": SIGNAL,
            "routine": "지표추종매매",
            "routine_instance_id": "INSTANCE-1",
            "code": CODE,
            "name": "삼성전자",
            "signal": "SELL",
            "status": "PENDING",
            "execution_intent": deepcopy(intents[0]),
            "execution_intents": deepcopy(intents),
        }])
        return self._inspect(current_price=current_price)

    def _inspect(
        self,
        *,
        current_price: int | None = 105,
        now: str = "2026-09-03T10:03:00",
    ) -> dict[str, object]:
        return inspect_sell_repeat_generations(
            selected_account_no=ACCOUNT,
            allowed_stock_codes=(CODE,),
            actionable_prices_by_code={CODE: current_price},
            now=datetime.fromisoformat(now),
            order_queue_path=self.queue,
            order_executions_path=self.executions,
            fills_path=self.fills,
            positions_path=self.positions,
            holdings_path=self.holdings,
            signals_path=self.signals,
        )

    def _set_exit_conditions(self, conditions: list[dict[str, object]]) -> None:
        for path, field in ((self.queue, "orders"), (self.signals, "signals")):
            root = json.loads(path.read_text(encoding="utf-8"))
            for record in root[field]:
                intents = []
                direct = record.get("execution_intent")
                if isinstance(direct, dict):
                    intents.append(direct)
                values = record.get("execution_intents")
                if isinstance(values, list):
                    intents.extend(item for item in values if isinstance(item, dict))
                for intent in intents:
                    policy = intent.get("sell_repeat_policy")
                    if not isinstance(policy, dict):
                        continue
                    policy["exit_policy"] = {
                        "policy": "SELL_REPEAT_EXIT",
                        "logic": "OR",
                        "conditions": deepcopy(conditions),
                        "snapshot_hash": "EXIT-POLICY-TEST-HASH",
                    }
            path.write_text(json.dumps(root), encoding="utf-8")

    def _set_direct_reset_threshold(self, threshold: float) -> None:
        for path, field in ((self.queue, "orders"), (self.signals, "signals")):
            root = json.loads(path.read_text(encoding="utf-8"))
            for record in root[field]:
                intents = []
                direct = record.get("execution_intent")
                if isinstance(direct, dict):
                    intents.append(direct)
                values = record.get("execution_intents")
                if isinstance(values, list):
                    intents.extend(item for item in values if isinstance(item, dict))
                for intent in intents:
                    intent["sell_price_reset_policy"] = {
                        "policy": "SELL_PRICE_CHANGE_RESET",
                        "action": "RESET",
                        "left_source": "ORDER_PRICE",
                        "right_source": "CURRENT_PRICE",
                        "direction": "UP",
                        "compare": ">=",
                        "threshold_percent": threshold,
                        "order_price": 100,
                    }
            path.write_text(json.dumps(root), encoding="utf-8")

    def _inspect_exit_only(self, *, current_price: int = 105) -> dict[str, object]:
        return inspect_sell_repeat_exits(
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

    def _inspect_reset(
        self,
        *,
        current_price: int = 105,
        blocked: tuple[str, ...] = (),
    ) -> dict[str, object]:
        return inspect_sell_price_resets(
            selected_account_no=ACCOUNT,
            allowed_stock_codes=(CODE,),
            actionable_prices_by_code={CODE: current_price},
            blocked_execution_process_ids=blocked,
            now=datetime.fromisoformat("2026-09-03T10:03:00"),
            order_queue_path=self.queue,
            order_executions_path=self.executions,
            fills_path=self.fills,
            positions_path=self.positions,
            holdings_path=self.holdings,
            signals_path=self.signals,
        )

    def test_exit_reset_precedence_matrix_uses_same_authoritative_fixture(self) -> None:
        cases = (
            ("both_true", 5, 5, 1, 0, 0),
            ("reset_only", 6, 5, 0, 1, 0),
            ("exit_only", 5, 6, 1, 0, 0),
            ("repeat_only", 6, 6, 0, 0, 1),
        )
        for name, exit_threshold, reset_threshold, exits, resets, repeats in cases:
            with self.subTest(name=name):
                self._fixture(status="FILLED", holding=3, current_price=105)
                self._set_exit_conditions([{
                    "condition_type": "PRICE",
                    "left_source": "CURRENT_PRICE",
                    "right_source": "ORDER_PRICE",
                    "direction": "UP",
                    "compare": ">=",
                    "threshold_percent": exit_threshold,
                }])
                self._set_direct_reset_threshold(reset_threshold)

                exit_result = self._inspect_exit_only()
                reset_result = self._inspect_reset(
                    blocked=tuple(exit_result["blocked_execution_process_ids"])
                )
                repeat_result = inspect_sell_repeat_generations(
                    selected_account_no=ACCOUNT,
                    allowed_stock_codes=(CODE,),
                    actionable_prices_by_code={CODE: 105},
                    blocked_execution_process_ids=tuple(
                        exit_result["blocked_execution_process_ids"]
                    ) + tuple(reset_result["blocked_execution_process_ids"]),
                    now=datetime.fromisoformat("2026-09-03T10:03:00"),
                    order_queue_path=self.queue,
                    order_executions_path=self.executions,
                    fills_path=self.fills,
                    positions_path=self.positions,
                    holdings_path=self.holdings,
                    signals_path=self.signals,
                )

                self.assertEqual(exits, len(exit_result["exit_proposals"]))
                self.assertEqual(resets, len(reset_result["replan_proposals"]))
                self.assertEqual(repeats, len(repeat_result["proposals"]))

    def test_timer_records_current_cycle_exit_before_reset_inspection(self) -> None:
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
        exit_result = {
            "ok": True,
            "proposals": [],
            "exit_proposals": [{
                "execution_process_id": PROCESS,
                "source_signal_id": SIGNAL,
            }],
            "blocked_execution_process_ids": [PROCESS],
            "reviews": [],
            "waiting": [],
            "errors": [],
        }
        empty = {"proposals": [], "reviews": [], "waiting": [], "errors": []}
        reset_empty = {
            **empty,
            "cancel_proposals": [],
            "replan_proposals": [],
            "blocked_execution_process_ids": [PROCESS],
        }
        consumer = {"summary": {"signals_checked": 0, "blocked": 0, "allowed": 0, "errors": 0, "orders_created": 0, "approval_checked": 0, "approved": 0, "executable_order_ids": []}}
        with (
            mock.patch.object(gui_auto_trade_timer, "inspect_sell_repeat_exits", return_value=exit_result),
            mock.patch.object(gui_auto_trade_timer, "record_repeat_sell_exit", return_value={"ok": True}) as record,
            mock.patch.object(gui_auto_trade_timer, "inspect_sell_price_resets", return_value=reset_empty) as reset,
            mock.patch.object(gui_auto_trade_timer, "enqueue_price_reset_generation") as enqueue_reset,
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

        record.assert_called_once()
        self.assertEqual((PROCESS,), reset.call_args.kwargs["blocked_execution_process_ids"])
        enqueue_reset.assert_not_called()
        self.assertEqual(1, result["repeat_sell"]["exits_recorded"])

    def _install_completed_repeat(self, proposal: dict[str, object]) -> None:
        queue_root = json.loads(self.queue.read_text(encoding="utf-8"))
        execution_root = json.loads(self.executions.read_text(encoding="utf-8"))
        for index, intent in enumerate(proposal["execution_intents"], start=1):
            queue_root["orders"].append({
                "id": f"QUEUE-REPEAT-{index}",
                "order_id": f"ORDER-REPEAT-{index}",
                "status": "FILLED",
                "source_signal_id": SIGNAL,
                "execution_process_id": PROCESS,
                "execution_id": intent["execution_id"],
                "plan_generation": proposal["plan_generation"],
                "option_snapshot_hash": "OPTION-HASH",
                "account_no": ACCOUNT,
                "code": CODE,
                "name": "삼성전자",
                "side": "SELL",
                "order_action": "NEW",
                "broker_order_no": f"BROKER-REPEAT-{index}",
                "remaining_quantity": 0,
                "quantity": intent["quantity"],
                "execution_intent": deepcopy(intent),
                "created_at": "2026-09-03T10:03:00",
                "updated_at": "2026-09-03T10:03:30",
            })
            execution_root["executions"].append({
                "execution_id": intent["execution_id"],
                "execution_process_id": PROCESS,
                "plan_generation": proposal["plan_generation"],
            })
        self._write(self.queue, orders=queue_root["orders"])
        self._write(
            self.executions,
            executions=execution_root["executions"],
            processes=execution_root["processes"],
        )
        signal_root = json.loads(self.signals.read_text(encoding="utf-8"))
        signal_root["signals"][0]["execution_intent"] = deepcopy(proposal["execution_intents"][0])
        signal_root["signals"][0]["execution_intents"] = deepcopy(proposal["execution_intents"])
        self._write(self.signals, signals=signal_root["signals"])
        holding_root = json.loads(self.holdings.read_text(encoding="utf-8"))
        holding_root["holdings"][0]["received_at"] = "2026-09-03T10:04:00"
        self._write(self.holdings, holdings=holding_root["holdings"])

    def test_active_generation_and_zero_holding_do_not_repeat(self) -> None:
        active = self._fixture(status="BROKER_ACCEPTED")
        self.assertEqual([], active["proposals"])
        self.assertEqual("SELL_REPEAT_PREVIOUS_GENERATION_ACTIVE", active["waiting"][0]["reason"])
        closed = self._fixture(holding=0)
        self.assertEqual([], closed["proposals"])
        self.assertEqual("SELL_REPEAT_POSITION_CLOSED", closed["waiting"][-1]["reason"])

    def test_exit_count_boundary_counts_completed_repeat_generations_only(self) -> None:
        self._fixture()
        self._set_exit_conditions([{
            "condition_type": "COUNT",
            "target_repeat_generations": 1,
            "initial_generation_included": False,
        }])
        before = self._inspect()
        self.assertEqual([], before["exit_proposals"])
        self.assertEqual(1, len(before["proposals"]))
        self._install_completed_repeat(before["proposals"][0])

        exact = self._inspect(now="2026-09-03T10:04:00")
        self.assertEqual([], exact["proposals"])
        self.assertEqual(1, len(exact["exit_proposals"]))
        evidence = exact["exit_proposals"][0]
        self.assertEqual("COUNT", evidence["exit_condition_type"])
        self.assertEqual(1, evidence["evaluated_generation"])

        self._set_exit_conditions([{
            "condition_type": "COUNT",
            "target_repeat_generations": 2,
            "initial_generation_included": False,
        }])
        second = self._inspect(now="2026-09-03T10:04:01")
        self._install_completed_repeat(second["proposals"][0])
        self._set_exit_conditions([{
            "condition_type": "COUNT",
            "target_repeat_generations": 1,
            "initial_generation_included": False,
        }])
        exceeded = self._inspect(now="2026-09-03T10:04:01")
        self.assertEqual(1, len(exceeded["exit_proposals"]))

    def test_exit_time_uses_first_repeat_generation_anchor_at_exact_boundary(self) -> None:
        self._fixture()
        self._set_exit_conditions([{"condition_type": "TIME", "duration_ms": 120_000, "anchor": "FIRST_REPEAT_GENERATION_AT"}])
        first = self._inspect(now="2026-09-03T10:03:00")
        self.assertEqual(1, len(first["proposals"]))
        self._install_completed_repeat(first["proposals"][0])

        before = self._inspect(now="2026-09-03T10:04:59.999")
        self.assertEqual([], before["exit_proposals"])
        self.assertEqual(1, len(before["proposals"]))
        exact = self._inspect(now="2026-09-03T10:05:00")
        self.assertEqual([], exact["proposals"])
        self.assertEqual("TIME", exact["exit_proposals"][0]["exit_condition_type"])
        after = self._inspect(now="2026-09-03T10:05:01")
        self.assertEqual(1, len(after["exit_proposals"]))

    def test_exit_price_exact_threshold_and_stale_current_price(self) -> None:
        self._fixture()
        self._set_exit_conditions([{
            "condition_type": "PRICE",
            "left_source": "CURRENT_PRICE",
            "right_source": "ORDER_PRICE",
            "direction": "UP",
            "compare": ">=",
            "threshold_percent": 5,
        }])
        below = self._inspect(current_price=104)
        self.assertEqual([], below["exit_proposals"])
        self.assertEqual(1, len(below["proposals"]))
        exact = self._inspect(current_price=105)
        self.assertEqual([], exact["proposals"])
        self.assertEqual("PRICE", exact["exit_proposals"][0]["exit_condition_type"])
        stale = self._inspect(current_price=None)
        self.assertEqual([], stale["proposals"])
        self.assertEqual([], stale["exit_proposals"])
        self.assertEqual("SELL_REPEAT_EXIT_EVIDENCE_PENDING", stale["waiting"][-1]["reason"])

    def test_multiple_exit_conditions_are_or_and_matched_count_does_not_need_price(self) -> None:
        self._fixture()
        self._set_exit_conditions([
            {"condition_type": "COUNT", "target_repeat_generations": 1, "initial_generation_included": False},
            {
                "condition_type": "PRICE",
                "left_source": "CURRENT_PRICE",
                "right_source": "AVG_PRICE",
                "direction": "UP",
                "compare": ">=",
                "threshold_percent": 50,
            },
        ])
        first = self._inspect(current_price=105)
        self._install_completed_repeat(first["proposals"][0])
        result = self._inspect(current_price=None)
        self.assertEqual([], result["proposals"])
        self.assertEqual(1, len(result["exit_proposals"]))
        self.assertEqual(["COUNT"], result["exit_proposals"][0]["exit_condition_types"])

    def test_terminal_generation_uses_latest_sellable_quantity_and_identity(self) -> None:
        result = self._fixture(holding=5, available=3, position=5)
        proposal = result["proposals"][0]
        intents = proposal["execution_intents"]
        self.assertEqual(3, sum(int(item["quantity"]) for item in intents))
        self.assertEqual({SIGNAL}, {item["source_signal_id"] for item in intents})
        self.assertEqual({PROCESS}, {item["execution_process_id"] for item in intents})
        self.assertEqual({1}, {item["plan_generation"] for item in intents})
        self.assertNotEqual(self._intents()[0]["execution_id"], intents[0]["execution_id"])
        self.assertEqual(len(intents), len({item["execution_id"] for item in intents}))

    def test_single_hoga_time_ratio_reuse_existing_generation_builder(self) -> None:
        for mode, kind in (
            ("SINGLE_ORDER", "SINGLE_ORDER"),
            ("MULTI_HOGA", "HOGA_LEVEL"),
            ("MULTI_TIME", "TIME_SLICE"),
            ("MULTI_RATIO", "RATIO_SLICE"),
        ):
            with self.subTest(mode=mode):
                proposal = self._fixture(mode=mode)["proposals"][0]
                intents = proposal["execution_intents"]
                self.assertEqual({kind}, {item["child_kind"] for item in intents})
                self.assertEqual({1}, {item["plan_generation"] for item in intents})
                self.assertTrue(all(item["repeat_generation"] is True for item in intents))
                self.assertTrue(all(item.get("unfilled_timeout_policy") for item in intents))
                self.assertTrue(all(item.get("sell_price_reset_policy") for item in intents))

    def test_current_price_is_fail_closed_and_position_mismatch_is_reviewed(self) -> None:
        unavailable = self._fixture(current_price=None)
        self.assertEqual([], unavailable["proposals"])
        self.assertEqual("SELL_REPEAT_CURRENT_PRICE_UNAVAILABLE", unavailable["waiting"][-1]["reason"])
        mismatch = self._fixture(holding=3, position=4)
        self.assertIn("SELL_REPEAT_POSITION_BROKER_MISMATCH", " ".join(mismatch["reviews"][0]["review_reasons"]))

    def test_uncertainty_and_terminal_rejection_are_not_repeat_retry(self) -> None:
        uncertain = self._fixture(status="SEND_UNCERTAIN")
        self.assertEqual([], uncertain["proposals"])
        self.assertIn("SELL_REPEAT_UNSAFE_ORDER", " ".join(uncertain["reviews"][0]["review_reasons"]))
        rejected = self._fixture(status="BROKER_REJECTED")
        self.assertEqual([], rejected["proposals"])
        self.assertIn("SELL_REPEAT_PREVIOUS_GENERATION_FAILURE_UNRESOLVED", " ".join(rejected["reviews"][0]["review_reasons"]))

    def test_active_cancel_blocks_and_timeout_cancel_allows_follow_up(self) -> None:
        active_cancel = {
            "id": "CANCEL-1",
            "status": "ORDER_QUEUED",
            "order_action": "CANCEL",
            "execution_process_id": PROCESS,
            "execution_request": {"request_preview": {"order_action": "CANCEL", "original_order_no": "BROKER-1"}},
        }
        active = self._fixture(extra_orders=[active_cancel])
        self.assertEqual([], active["proposals"])
        self.assertEqual("SELL_REPEAT_ACTIVE_CANCEL", active["waiting"][-1]["reason"])

        timeout_cancel = deepcopy(active_cancel)
        timeout_cancel.update({
            "status": "CANCELLED",
            "original_order_effect_confirmed": True,
            "cancel_evidence": {"trigger": "UNFILLED_TIMEOUT"},
            "updated_at": "2026-09-03T10:00:30",
        })
        allowed = self._fixture(status="CANCELLED", extra_orders=[timeout_cancel])
        self.assertEqual(1, len(allowed["proposals"]))

    def test_duplicate_snapshot_and_restart_pending_generation_are_idempotent(self) -> None:
        proposal = self._fixture()["proposals"][0]
        signal_data = json.loads(self.signals.read_text(encoding="utf-8"))
        signal_data["signals"][0]["execution_intent"] = proposal["execution_intents"][0]
        signal_data["signals"][0]["execution_intents"] = proposal["execution_intents"]
        self.signals.write_text(json.dumps(signal_data), encoding="utf-8")
        replay = self._inspect()
        self.assertEqual([], replay["proposals"])
        self.assertEqual("SELL_REPEAT_GENERATION_PENDING_EXECUTION", replay["waiting"][-1]["reason"])

    def test_exit_evidence_uses_canonical_writer_and_blocks_restart_repeat(self) -> None:
        self._fixture()
        self._set_exit_conditions([{
            "condition_type": "PRICE",
            "left_source": "CURRENT_PRICE",
            "right_source": "ORDER_PRICE",
            "direction": "UP",
            "compare": ">=",
            "threshold_percent": 5,
        }])
        proposal = self._inspect(current_price=105)["exit_proposals"][0]
        with mock.patch.object(routine_signal_consumer, "update_signal_status", return_value={"ok": True}) as update:
            recorded = routine_signal_consumer.record_repeat_sell_exit(proposal)
        self.assertTrue(recorded["ok"])
        metadata = update.call_args.kwargs["metadata"]
        evidence = metadata["sell_repeat_exit_evidence"]
        self.assertEqual(PROCESS, evidence["execution_process_id"])
        self.assertEqual(proposal["exit_source_snapshot_hash"], evidence["exit_source_snapshot_hash"])

        signal_root = json.loads(self.signals.read_text(encoding="utf-8"))
        signal_root["signals"][0]["sell_repeat_exit_evidence"] = evidence
        self._write(self.signals, signals=signal_root["signals"])
        restarted = self._inspect(current_price=105)
        self.assertEqual([], restarted["proposals"])
        self.assertEqual([], restarted["exit_proposals"])
        self.assertEqual("SELL_REPEAT_EXIT_ALREADY_RECORDED", restarted["waiting"][-1]["reason"])

    def test_enqueue_uses_canonical_signal_writer_and_deferred_modes(self) -> None:
        time_proposal = self._fixture(mode="MULTI_TIME")["proposals"][0]
        with mock.patch.object(routine_signal_consumer, "update_signal_status", return_value={"ok": True}) as update:
            deferred = routine_signal_consumer.enqueue_repeat_sell_generation(time_proposal)
        self.assertTrue(deferred["ok"])
        self.assertTrue(deferred["deferred"])
        self.assertEqual("PENDING", update.call_args.args[1])

        single = self._fixture()["proposals"][0]
        with (
            mock.patch.object(routine_signal_consumer, "update_signal_status", return_value={"ok": True}) as update,
            mock.patch.object(routine_signal_consumer, "enqueue_replanned_execution_intents", return_value={"ok": True, "orders_created": 1, "executable_order_ids": ["ORDER-NEW"]}) as enqueue,
        ):
            immediate = routine_signal_consumer.enqueue_repeat_sell_generation(single)
        self.assertTrue(immediate["ok"])
        self.assertEqual(1, immediate["orders_created"])
        enqueue.assert_called_once()
        self.assertEqual(2, update.call_count)

    def test_repeat_reenters_candidate_approval_and_operation_policy_pipeline(self) -> None:
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
            mock.patch.object(routine_signal_consumer, "routine_execution_intent_admission", return_value={"allowed": True}),
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
            result = routine_signal_consumer.enqueue_repeat_sell_generation(proposal)

        self.assertTrue(result["ok"], result)
        self.assertEqual(1, result["orders_created"])
        self.assertEqual(1, result["approved"])
        self.assertEqual(1, result["policy_executable"])
        self.assertEqual(1, len(result["executable_order_ids"]))
        self.assertEqual(1, len(captured))
        self.assertEqual(1, captured[0]["plan_generation"])
        self.assertEqual(SIGNAL, captured[0]["source_signal_id"])
        self.assertEqual(PROCESS, captured[0]["execution_process_id"])

    def test_timer_routes_repeat_after_other_progression_without_global_stop(self) -> None:
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
        reset_empty = {**empty, "cancel_proposals": [], "replan_proposals": [], "blocked_execution_process_ids": []}
        repeat = {
            "proposals": [{"signal": {"id": SIGNAL}, "execution_intents": [{}]}],
            "reviews": [{"code": CODE, "review_reasons": ["REPEAT-REVIEW"]}],
            "waiting": [],
            "errors": [],
        }
        consumer = {"summary": {"signals_checked": 0, "blocked": 0, "allowed": 0, "errors": 0, "orders_created": 0, "approval_checked": 0, "approved": 0, "executable_order_ids": []}}
        with (
            mock.patch.object(gui_auto_trade_timer, "inspect_sell_price_resets", return_value=reset_empty),
            mock.patch.object(gui_auto_trade_timer, "inspect_unfilled_sell_cancel_eligibility", return_value=empty),
            mock.patch.object(gui_auto_trade_timer, "inspect_due_time_slices", return_value=empty),
            mock.patch.object(gui_auto_trade_timer, "inspect_eligible_ratio_slices", return_value=empty),
            mock.patch.object(gui_auto_trade_timer, "inspect_execution_process_supplements", return_value=empty),
            mock.patch.object(gui_auto_trade_timer, "inspect_sell_repeat_generations", return_value=repeat),
            mock.patch.object(gui_auto_trade_timer, "enqueue_repeat_sell_generation", return_value={"ok": True, "orders_created": 1, "executable_order_ids": ["ORDER-REPEAT"]}),
            mock.patch.object(gui_auto_trade_timer, "consume_pending_routine_signals_dry_run", return_value=consumer),
            mock.patch.object(gui_auto_trade_timer, "auto_trade_signal_probe_only_active", return_value=True),
            mock.patch.object(gui_auto_trade_timer, "auto_trade_real_execution_active", return_value=False),
            mock.patch.object(gui_auto_trade_timer, "actionable_current_price", return_value=105),
        ):
            result = gui_auto_trade_timer._process_pending_signal_pipeline(window, snapshot)
        self.assertEqual(1, result["repeat_sell"]["orders_created"])
        self.assertEqual(1, result["repeat_sell"]["reviews"])

    def test_timer_records_exit_and_still_enqueues_other_process_repeat(self) -> None:
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
        reset_empty = {**empty, "cancel_proposals": [], "replan_proposals": [], "blocked_execution_process_ids": []}
        repeat = {
            "proposals": [{"execution_process_id": "PROCESS-OTHER", "signal": {"id": "SIGNAL-OTHER"}, "execution_intents": [{}]}],
            "exit_proposals": [{"execution_process_id": PROCESS, "source_signal_id": SIGNAL}],
            "reviews": [],
            "waiting": [],
            "errors": [],
        }
        consumer = {"summary": {"signals_checked": 0, "blocked": 0, "allowed": 0, "errors": 0, "orders_created": 0, "approval_checked": 0, "approved": 0, "executable_order_ids": []}}
        with (
            mock.patch.object(gui_auto_trade_timer, "inspect_sell_price_resets", return_value=reset_empty),
            mock.patch.object(gui_auto_trade_timer, "inspect_unfilled_sell_cancel_eligibility", return_value=empty),
            mock.patch.object(gui_auto_trade_timer, "inspect_due_time_slices", return_value=empty),
            mock.patch.object(gui_auto_trade_timer, "inspect_eligible_ratio_slices", return_value=empty),
            mock.patch.object(gui_auto_trade_timer, "inspect_execution_process_supplements", return_value=empty),
            mock.patch.object(gui_auto_trade_timer, "inspect_sell_repeat_generations", return_value=repeat),
            mock.patch.object(gui_auto_trade_timer, "record_repeat_sell_exit", return_value={"ok": True}) as record,
            mock.patch.object(gui_auto_trade_timer, "enqueue_repeat_sell_generation", return_value={"ok": True, "orders_created": 1, "executable_order_ids": ["ORDER-OTHER"]}) as enqueue,
            mock.patch.object(gui_auto_trade_timer, "consume_pending_routine_signals_dry_run", return_value=consumer),
            mock.patch.object(gui_auto_trade_timer, "auto_trade_signal_probe_only_active", return_value=True),
            mock.patch.object(gui_auto_trade_timer, "auto_trade_real_execution_active", return_value=False),
            mock.patch.object(gui_auto_trade_timer, "actionable_current_price", return_value=105),
        ):
            result = gui_auto_trade_timer._process_pending_signal_pipeline(window, snapshot)
        self.assertEqual(1, result["repeat_sell"]["exits_recorded"])
        self.assertEqual(1, result["repeat_sell"]["orders_created"])
        record.assert_called_once()
        enqueue.assert_called_once()


if __name__ == "__main__":
    unittest.main()
