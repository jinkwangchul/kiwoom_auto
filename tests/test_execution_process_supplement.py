# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from execution_process_supplement import inspect_execution_process_supplements
from execution_provenance_contract import plan_generation
import order_queue
import routine_signal_consumer
import gui_auto_trade_timer


class ExecutionProcessSupplementTest(unittest.TestCase):
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
    def _template(*, process: str, signal: str, generation: int = 0) -> dict[str, object]:
        return {
            "side": "SELL",
            "quantity": 3,
            "planned_total_quantity": 13,
            "budget": None,
            "price_basis": "ORDER_PRICE",
            "price": 81_000,
            "hoga": "LIMIT",
            "routine_type": "INDICATOR_FOLLOW",
            "routine_instance_id": "INSTANCE-1",
            "source_signal_id": signal,
            "execution_process_id": process,
            "execution_mode": "MULTI_HOGA",
            "execution_process_owner_required": generation == 0,
            "plan_generation": generation,
            "child_sequence_index": 1,
            "child_sequence_total": 5,
            "child_kind": "HOGA_LEVEL",
            "child_plan": {
                "planned_quantity": 3,
                "planned_price": 81_000,
                "hoga_offset_ticks": 0,
            },
            "multi_hoga_plan": {
                "base_price": 81_000,
                "hoga_offsets": [0, 1, -1, 2, -2],
                "planned_child_count": 5,
                "planned_total_quantity": 13,
                "instrument_type": "일반종목",
            },
        }

    def _order(
        self,
        index: int,
        quantity: int,
        *,
        process: str = "PROCESS-1",
        signal: str = "SIGNAL-1",
        code: str = "005930",
        status: str = "BROKER_ACCEPTED",
        generation: int = 0,
        remaining: int | None = None,
        filled: int | None = None,
    ) -> dict[str, object]:
        intent = self._template(process=process, signal=signal, generation=generation)
        intent.update(
            {
                "quantity": quantity,
                "child_sequence_index": index,
                "child_sequence_total": 5 if generation == 0 else 1,
                "child_plan": {
                    "planned_quantity": quantity,
                    "planned_price": 81_000,
                    "hoga_offset_ticks": 0,
                },
            }
        )
        record: dict[str, object] = {
            "id": f"ORDER_QUEUED_{process}_{generation}_{index}",
            "source": "execution_queue_pending",
            "status": status,
            "source_signal_id": signal,
            "execution_process_id": process,
            "execution_id": f"EXEC_{process}_{generation}_{index}",
            "plan_generation": generation,
            "child_sequence_index": index,
            "child_sequence_total": 5 if generation == 0 else 1,
            "child_kind": "HOGA_LEVEL",
            "child_plan": intent["child_plan"],
            "option_snapshot_hash": f"HASH-{process}",
            "routine": "지표추종매매",
            "routine_instance_id": "INSTANCE-1",
            "code": code,
            "name": code,
            "side": "SELL",
            "quantity": quantity,
            "execution_intent": intent,
            "updated_at": "2026-09-02T10:00:00",
        }
        if remaining is not None:
            record["remaining_quantity"] = remaining
        if filled is not None:
            record["total_filled_quantity"] = filled
        return record

    def _fixture(
        self,
        *,
        process: str = "PROCESS-1",
        signal: str = "SIGNAL-1",
        code: str = "005930",
        failed_index: int = 3,
        available: int = 3,
        uncertain_index: int | None = None,
        position_quantity: int = 13,
        holding_quantity: int = 13,
        partial: bool = False,
    ) -> None:
        quantities = [3, 3, 3, 2, 2]
        orders: list[dict[str, object]] = []
        runtime: list[dict[str, object]] = []
        fills: list[dict[str, object]] = []
        for index, quantity in enumerate(quantities, start=1):
            if index == uncertain_index:
                status = "SEND_UNCERTAIN"
                remaining = None
                filled = None
            elif index == failed_index:
                status = "SEND_CALL_REJECTED"
                remaining = None
                filled = None
            elif partial and index == 1:
                status = "PARTIALLY_FILLED"
                remaining = 2
                filled = 1
                fills.append(
                    {
                        "fill_id": f"FILL-{process}-1",
                        "execution_id": f"EXEC_{process}_0_1",
                        "execution_process_id": process,
                        "filled_quantity": 1,
                        "remaining_quantity": 2,
                    }
                )
            else:
                status = "BROKER_ACCEPTED"
                remaining = quantity
                filled = None
            order = self._order(
                index,
                quantity,
                process=process,
                signal=signal,
                code=code,
                status=status,
                remaining=remaining,
                filled=filled,
            )
            if status == "SEND_UNCERTAIN":
                order["send_uncertain"] = True
                order["manual_reconciliation_required"] = True
            orders.append(order)
            runtime.append(
                {
                    "execution_id": order["execution_id"],
                    "execution_process_id": process,
                    "plan_generation": 0,
                    "child_sequence_index": index,
                    "child_sequence_total": 5,
                    "child_kind": "HOGA_LEVEL",
                    "child_plan": order["child_plan"],
                    "option_snapshot_hash": f"HASH-{process}",
                }
            )
        self._write(self.queue, orders=orders)
        self._write(
            self.executions,
            executions=runtime,
            processes=[
                {
                    "execution_process_id": process,
                    "option_snapshot_hash": f"HASH-{process}",
                }
            ],
        )
        self._write(self.fills, fills=fills)
        self._write(
            self.positions,
            positions=[
                {
                    "account_no": "12345678",
                    "code": code,
                    "quantity": position_quantity,
                }
            ],
        )
        self._write(
            self.holdings,
            holdings=[
                {
                    "account_no": "12345678",
                    "code": code,
                    "holding_quantity": holding_quantity,
                    "available_quantity": available,
                    "received_at": "2026-09-02T10:01:00",
                    "reconciliation_status": "CONSISTENT",
                    "manual_reconciliation_required": False,
                }
            ],
        )
        self._write(
            self.signals,
            signals=[
                {
                    "id": signal,
                    "routine": "지표추종매매",
                    "routine_instance_id": "INSTANCE-1",
                    "code": code,
                    "name": code,
                    "signal": "SELL",
                    "status": "PREVIEWED",
                }
            ],
        )

    def _inspect(self, *, allowed: tuple[str, ...] = ("005930",)) -> dict[str, object]:
        return inspect_execution_process_supplements(
            selected_account_no="12345678",
            allowed_stock_codes=allowed,
            order_queue_path=self.queue,
            order_executions_path=self.executions,
            fills_path=self.fills,
            positions_path=self.positions,
            broker_holdings_path=self.holdings,
            routine_signals_path=self.signals,
        )

    def test_missing_generation_is_legacy_zero(self) -> None:
        self.assertEqual(0, plan_generation(None))
        self.assertEqual(0, plan_generation(""))
        self.assertEqual(0, plan_generation(0))

    def test_confirmed_failure_creates_generation_one_single_base_supplement(self) -> None:
        self._fixture()

        result = self._inspect()

        self.assertTrue(result["ok"], result)
        self.assertEqual(1, len(result["proposals"]))
        proposal = result["proposals"][0]
        self.assertEqual(3, proposal["candidate_shortfall_quantity"])
        self.assertEqual(3, proposal["supplement_quantity"])
        self.assertEqual(1, proposal["plan_generation"])
        intents = proposal["execution_intents"]
        self.assertEqual(1, len(intents))
        self.assertEqual(3, intents[0]["quantity"])
        self.assertEqual(81_000, intents[0]["price"])
        self.assertEqual(0, intents[0]["child_plan"]["hoga_offset_ticks"])
        self.assertEqual("SIGNAL-1", intents[0]["source_signal_id"])
        self.assertEqual("PROCESS-1", intents[0]["execution_process_id"])
        self.assertEqual(1, intents[0]["plan_generation"])

    def test_open_remaining_and_partial_fill_are_not_over_supplemented(self) -> None:
        self._fixture(
            partial=True,
            position_quantity=12,
            holding_quantity=12,
        )

        proposal = self._inspect()["proposals"][0]

        self.assertEqual(1, proposal["confirmed_filled_quantity"])
        self.assertEqual(9, proposal["confirmed_live_remaining_quantity"])
        self.assertEqual(3, proposal["candidate_shortfall_quantity"])
        self.assertEqual(3, proposal["supplement_quantity"])

    def test_latest_sellable_quantity_caps_supplement(self) -> None:
        self._fixture(available=1)

        proposal = self._inspect()["proposals"][0]

        self.assertEqual(3, proposal["candidate_shortfall_quantity"])
        self.assertEqual(1, proposal["supplement_quantity"])
        self.assertEqual(1, proposal["execution_intents"][0]["quantity"])

    def test_no_sellable_quantity_is_noop(self) -> None:
        self._fixture(available=0)

        result = self._inspect()

        self.assertEqual([], result["proposals"])
        self.assertEqual("NO_SUPPLEMENT_REQUIRED", result["waiting"][0]["reason"])

    def test_send_uncertain_is_review_not_supplement(self) -> None:
        self._fixture(uncertain_index=3)

        result = self._inspect()

        self.assertEqual([], result["proposals"])
        self.assertEqual(1, len(result["reviews"]))
        self.assertTrue(
            any("UNSAFE_CHILD" in reason for reason in result["reviews"][0]["review_reasons"])
        )

    def test_position_holding_mismatch_requires_review(self) -> None:
        self._fixture(position_quantity=12, holding_quantity=13)

        result = self._inspect()

        self.assertEqual([], result["proposals"])
        self.assertIn(
            "POSITION_BROKER_HOLDING_MISMATCH",
            result["reviews"][0]["review_reasons"],
        )

    def test_holding_snapshot_older_than_fill_evidence_waits(self) -> None:
        self._fixture(
            partial=True,
            position_quantity=12,
            holding_quantity=12,
        )
        root = json.loads(self.fills.read_text(encoding="utf-8"))
        root["fills"][0]["recorded_at"] = "2026-09-02T10:02:00"
        self._write(self.fills, fills=root["fills"])

        result = self._inspect()

        self.assertEqual([], result["proposals"])
        self.assertEqual(
            "POST_BATCH_HOLDING_EVIDENCE_PENDING",
            result["waiting"][0]["reason"],
        )

    def test_uncertain_process_does_not_block_other_stock_proposal(self) -> None:
        self._fixture(uncertain_index=3)
        first_queue = json.loads(self.queue.read_text(encoding="utf-8"))["orders"]
        first_exec_root = json.loads(self.executions.read_text(encoding="utf-8"))
        first_positions = json.loads(self.positions.read_text(encoding="utf-8"))["positions"]
        first_holdings = json.loads(self.holdings.read_text(encoding="utf-8"))["holdings"]
        first_signals = json.loads(self.signals.read_text(encoding="utf-8"))["signals"]
        self._fixture(process="PROCESS-2", signal="SIGNAL-2", code="000660")
        second_queue = json.loads(self.queue.read_text(encoding="utf-8"))["orders"]
        second_exec_root = json.loads(self.executions.read_text(encoding="utf-8"))
        second_positions = json.loads(self.positions.read_text(encoding="utf-8"))["positions"]
        second_holdings = json.loads(self.holdings.read_text(encoding="utf-8"))["holdings"]
        second_signals = json.loads(self.signals.read_text(encoding="utf-8"))["signals"]
        self._write(self.queue, orders=first_queue + second_queue)
        self._write(
            self.executions,
            executions=first_exec_root["executions"] + second_exec_root["executions"],
            processes=first_exec_root["processes"] + second_exec_root["processes"],
        )
        self._write(self.positions, positions=first_positions + second_positions)
        self._write(self.holdings, holdings=first_holdings + second_holdings)
        self._write(self.signals, signals=first_signals + second_signals)

        result = self._inspect(allowed=("005930", "000660"))

        self.assertEqual(["PROCESS-1"], [item["execution_process_id"] for item in result["reviews"]])
        self.assertEqual(["PROCESS-2"], [item["execution_process_id"] for item in result["proposals"]])

    def test_queue_allows_generation_one_sequence_one_and_dedupes_replay(self) -> None:
        base = {
            "source_signal_id": "SIGNAL-1",
            "routine": "지표추종매매",
            "code": "005930",
            "side": "SELL",
            "execution_process_id": "PROCESS-1",
            "execution_id": "EXEC-G0-1",
            "plan_generation": 0,
            "child_sequence_index": 1,
            "child_sequence_total": 2,
        }
        other = {**base, "execution_id": "EXEC-G0-2", "child_sequence_index": 2}
        supplement = {
            **base,
            "execution_id": "EXEC-G1-1",
            "plan_generation": 1,
            "child_sequence_index": 1,
            "child_sequence_total": 1,
        }
        self.assertIsNone(order_queue._candidate_duplicate_reason(other, [base]))
        self.assertIsNone(order_queue._candidate_duplicate_reason(supplement, [base, other]))
        self.assertIsNotNone(order_queue._candidate_duplicate_reason(dict(supplement), [supplement]))

    def test_same_replan_snapshot_does_not_create_another_generation(self) -> None:
        self._fixture()
        first = self._inspect()["proposals"][0]
        root = json.loads(self.queue.read_text(encoding="utf-8"))
        intent = first["execution_intents"][0]
        root["orders"].append(
            {
                "id": "ORDER_QUEUED_PROCESS-1_1_1",
                "source": "execution_queue_pending",
                "status": "EXECUTABLE",
                "source_signal_id": first["source_signal_id"],
                "execution_process_id": first["execution_process_id"],
                "execution_id": intent["execution_id"],
                "plan_generation": 1,
                "child_sequence_index": 1,
                "child_sequence_total": 1,
                "child_kind": "HOGA_LEVEL",
                "child_plan": intent["child_plan"],
                "option_snapshot_hash": "HASH-PROCESS-1",
                "routine": "지표추종매매",
                "routine_instance_id": "INSTANCE-1",
                "code": "005930",
                "name": "005930",
                "side": "SELL",
                "quantity": intent["quantity"],
                "execution_intent": intent,
                "updated_at": "2026-09-02T10:02:00",
            }
        )
        self._write(self.queue, orders=root["orders"])

        second = self._inspect()

        self.assertEqual([], second["proposals"])
        self.assertEqual("BROKER_EVIDENCE_PENDING", second["waiting"][0]["reason"])

    def test_missing_generation_child_is_reviewed_not_supplemented(self) -> None:
        self._fixture()
        root = json.loads(self.queue.read_text(encoding="utf-8"))
        root["orders"] = [
            item for item in root["orders"] if item["child_sequence_index"] != 5
        ]
        self._write(self.queue, orders=root["orders"])

        result = self._inspect()

        self.assertEqual([], result["proposals"])
        self.assertTrue(
            any(
                "CHILD_SET_INVALID" in reason
                for reason in result["reviews"][0]["review_reasons"]
            )
        )

    def test_supplement_reenters_candidate_approval_and_policy_pipeline(self) -> None:
        self._fixture()
        proposal = self._inspect()["proposals"][0]
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

        with mock.patch.object(
            routine_signal_consumer,
            "routine_execution_intent_admission",
            return_value={"allowed": True},
        ), mock.patch.object(
            routine_signal_consumer,
            "read_order_queue",
            return_value={"orders": []},
        ), mock.patch.object(
            routine_signal_consumer,
            "append_order_candidates",
            side_effect=append,
        ), mock.patch.object(
            routine_signal_consumer,
            "evaluate_order_approval",
            return_value={"approval_status": "APPROVED", "approval_reason": ""},
        ), mock.patch.object(
            routine_signal_consumer.operation_policy_gate,
            "apply_operation_policy_gate_for_order",
            side_effect=lambda order_id, **_kwargs: {
                "ok": True,
                "status": "allowed",
                "after_status": "EXECUTABLE",
                "policy_status": "EXECUTABLE",
                "reason": "",
            },
        ):
            result = routine_signal_consumer.enqueue_replanned_execution_intents(
                proposal["signal"],
                proposal["execution_intents"],
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual(1, result["orders_created"])
        self.assertEqual(1, result["approved"])
        self.assertEqual(1, result["policy_executable"])
        self.assertEqual(1, len(result["executable_order_ids"]))
        self.assertEqual(1, len(captured))
        self.assertEqual(1, captured[0]["plan_generation"])
        self.assertEqual("SIGNAL-1", captured[0]["source_signal_id"])
        self.assertEqual("PROCESS-1", captured[0]["execution_process_id"])
        self.assertNotEqual("EXEC_PROCESS-1_0_3", captured[0]["execution_id"])

    def test_timer_reviews_uncertain_stock_and_executes_other_stock_supplement(self) -> None:
        window = SimpleNamespace(
            _selected_account_no=lambda: "12345678",
            mark_review_required=mock.Mock(return_value=True),
            statusBarMessage=mock.Mock(),
            auto_process_executable_orders_for_real_trade=mock.Mock(
                return_value={"processed": 1, "blocked": 0}
            ),
        )
        snapshot = SimpleNamespace(
            entries=(
                SimpleNamespace(
                    execution_ready=True,
                    real_trade_enabled=True,
                    signal_probe_only=False,
                    stock_code="005930",
                    stock_name="삼성전자",
                    stock_dir=self.root / "005930",
                ),
                SimpleNamespace(
                    execution_ready=True,
                    real_trade_enabled=True,
                    signal_probe_only=False,
                    stock_code="000660",
                    stock_name="SK하이닉스",
                    stock_dir=self.root / "000660",
                ),
            )
        )
        inspected = {
            "ok": True,
            "proposals": [
                {
                    "code": "000660",
                    "signal": {"id": "SIGNAL-2"},
                    "execution_intents": [{"execution_id": "EXEC-G1-1"}],
                }
            ],
            "reviews": [
                {
                    "code": "005930",
                    "review_reasons": ["UNSAFE_CHILD:EXEC-1:SEND_UNCERTAIN"],
                }
            ],
            "waiting": [],
            "errors": [],
        }
        empty_summary = {
            "signals_checked": 0,
            "blocked": 0,
            "allowed": 0,
            "errors": 0,
            "orders_created": 0,
            "approval_checked": 0,
            "approved": 0,
            "executable_order_ids": [],
        }
        with mock.patch.object(
            gui_auto_trade_timer,
            "inspect_execution_process_supplements",
            return_value=inspected,
        ) as inspect, mock.patch.object(
            gui_auto_trade_timer,
            "enqueue_replanned_execution_intents",
            return_value={
                "ok": True,
                "orders_created": 1,
                "executable_order_ids": ["ORDER-SUPPLEMENT-1"],
            },
        ) as enqueue, mock.patch.object(
            gui_auto_trade_timer,
            "consume_pending_routine_signals_dry_run",
            return_value={"summary": empty_summary},
        ), mock.patch.object(
            gui_auto_trade_timer,
            "auto_trade_signal_probe_only_active",
            return_value=False,
        ), mock.patch.object(
            gui_auto_trade_timer,
            "auto_trade_real_execution_active",
            return_value=True,
        ):
            result = gui_auto_trade_timer._process_pending_signal_pipeline(window, snapshot)

        inspect.assert_called_once()
        enqueue.assert_called_once()
        window.mark_review_required.assert_called_once()
        window.auto_process_executable_orders_for_real_trade.assert_called_once_with(
            limit=5,
            order_ids=["ORDER-SUPPLEMENT-1"],
        )
        self.assertEqual(1, result["supplement"]["reviews"])
        self.assertEqual(1, result["supplement"]["orders_created"])


if __name__ == "__main__":
    unittest.main()
