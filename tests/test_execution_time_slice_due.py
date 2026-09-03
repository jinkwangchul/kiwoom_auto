# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from execution_time_slice_due import inspect_due_time_slices
import routine_signal_consumer
import routine_signal_queue
import gui_auto_trade_timer
import order_queue
from types import SimpleNamespace


class ExecutionTimeSliceDueTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.signals = self.root / "routine_signals.json"
        self.orders = self.root / "order_queue.json"
        self.executions = self.root / "order_executions.json"
        self.fills = self.root / "fills.json"
        self.positions = self.root / "positions.json"
        self.holdings = self.root / "broker_holdings.json"
        self.process_id = "PROCESS-TIME-1"
        self.signal_id = "SIGNAL-TIME-1"
        self.code = "005930"

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _write(path: Path, **fields: object) -> None:
        path.write_text(
            json.dumps({"version": 1, **fields}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _intents(self) -> list[dict[str, object]]:
        quantities = [4, 3, 3]
        schedules = [
            "2026-09-02T10:00:00.000",
            "2026-09-02T10:00:15.000",
            "2026-09-02T10:00:30.000",
        ]
        values: list[dict[str, object]] = []
        for index, (quantity, scheduled_at) in enumerate(zip(quantities, schedules), start=1):
            values.append(
                {
                    "execution_id": f"EXEC-TIME-{index}",
                    "side": "SELL",
                    "quantity": quantity,
                    "planned_total_quantity": 10,
                    "price_basis": "ORDER_PRICE",
                    "price": 81_000,
                    "hoga": "LIMIT",
                    "routine_type": "INDICATOR_FOLLOW",
                    "routine_instance_id": "INSTANCE-1",
                    "source_signal_id": self.signal_id,
                    "execution_process_id": self.process_id,
                    "execution_mode": "MULTI_TIME",
                    "execution_process_owner_required": True,
                    "plan_generation": 0,
                    "child_sequence_index": index,
                    "child_sequence_total": 3,
                    "child_kind": "TIME_SLICE",
                    "child_plan": {
                        "planned_quantity": quantity,
                        "planned_price": 81_000,
                        "scheduled_offset_ms": (index - 1) * 15_000,
                        "schedule_anchor_at": "2026-09-02T10:00:00",
                        "scheduled_at": scheduled_at,
                    },
                    "multi_time_plan": {
                        "planned_child_count": 3,
                        "planned_total_quantity": 10,
                        "scheduled_offsets_ms": [0, 15_000, 30_000],
                    },
                }
            )
        return values

    def _order(
        self,
        index: int,
        *,
        status: str = "BROKER_ACCEPTED",
        remaining: int | None = None,
        updated_at: str = "2026-09-02T10:00:05",
    ) -> dict[str, object]:
        intent = deepcopy(self._intents()[index - 1])
        record: dict[str, object] = {
            "id": f"ORDER_QUEUED_TIME_{index}",
            "status": status,
            "source": "execution_queue_pending",
            "source_signal_id": self.signal_id,
            "execution_process_id": self.process_id,
            "execution_id": intent["execution_id"],
            "plan_generation": 0,
            "child_sequence_index": index,
            "child_sequence_total": 3,
            "child_kind": "TIME_SLICE",
            "child_plan": intent["child_plan"],
            "code": self.code,
            "quantity": intent["quantity"],
            "execution_intent": intent,
            "updated_at": updated_at,
        }
        if remaining is not None:
            record["remaining_quantity"] = remaining
        if status == "SEND_UNCERTAIN":
            record["send_uncertain"] = True
            record["manual_reconciliation_required"] = True
        return record

    def _runtime(self, index: int) -> dict[str, object]:
        intent = self._intents()[index - 1]
        return {
            "execution_id": intent["execution_id"],
            "execution_process_id": self.process_id,
            "plan_generation": 0,
            "child_sequence_index": index,
            "child_sequence_total": 3,
            "child_kind": "TIME_SLICE",
            "child_plan": intent["child_plan"],
        }

    def _fixture(
        self,
        *,
        orders: list[dict[str, object]] | None = None,
        runtimes: list[dict[str, object]] | None = None,
        fills: list[dict[str, object]] | None = None,
        holding_quantity: int = 10,
        available_quantity: int = 10,
        position_quantity: int = 10,
        received_at: str = "2026-09-02T10:01:00",
    ) -> None:
        self._write(
            self.signals,
            signals=[
                {
                    "id": self.signal_id,
                    "status": "PENDING",
                    "routine": "지표추종매매",
                    "routine_instance_id": "INSTANCE-1",
                    "code": self.code,
                    "name": "삼성전자",
                    "signal": "SELL",
                    "execution_intent": self._intents()[0],
                    "execution_intents": self._intents(),
                }
            ],
        )
        self._write(self.orders, orders=orders or [])
        self._write(
            self.executions,
            executions=runtimes or [],
            processes=(
                [{"execution_process_id": self.process_id}]
                if (orders or runtimes)
                else []
            ),
        )
        self._write(self.fills, fills=fills or [])
        self._write(
            self.positions,
            positions=[
                {
                    "account_no": "12345678",
                    "code": self.code,
                    "quantity": position_quantity,
                    "updated_at": "2026-09-02T10:00:30",
                }
            ],
        )
        self._write(
            self.holdings,
            holdings=[
                {
                    "account_no": "12345678",
                    "code": self.code,
                    "holding_quantity": holding_quantity,
                    "available_quantity": available_quantity,
                    "received_at": received_at,
                    "reconciliation_status": "CONSISTENT",
                    "manual_reconciliation_required": False,
                }
            ],
        )

    def _inspect(self, at: str) -> dict[str, object]:
        return inspect_due_time_slices(
            selected_account_no="12345678",
            allowed_stock_codes=(self.code,),
            now=datetime.fromisoformat(at),
            signals_path=self.signals,
            orders_path=self.orders,
            executions_path=self.executions,
            fills_path=self.fills,
            positions_path=self.positions,
            holdings_path=self.holdings,
        )

    def test_not_due_then_due_and_overdue_cycles_select_one_child_only(self) -> None:
        self._fixture()

        before = self._inspect("2026-09-02T09:59:59")
        first = self._inspect("2026-09-02T10:00:00")
        overdue = self._inspect("2026-09-02T10:01:00")

        self.assertEqual([], before["proposals"])
        self.assertEqual(1, first["proposals"][0]["child_sequence_index"])
        self.assertEqual(4, first["proposals"][0]["safe_quantity"])
        self.assertEqual(1, len(overdue["proposals"]))
        self.assertEqual(1, overdue["proposals"][0]["child_sequence_index"])
        self.assertEqual(3, overdue["proposals"][0]["overdue_child_count"])

    def test_existing_or_pending_child_is_not_recreated(self) -> None:
        first = self._order(1)
        self._fixture(orders=[first], runtimes=[self._runtime(1)])

        second = self._inspect("2026-09-02T10:00:15")

        self.assertEqual(2, second["proposals"][0]["child_sequence_index"])
        self.assertEqual(3, second["proposals"][0]["safe_quantity"])

        pending = self._order(1, status="ORDER_QUEUED")
        self._fixture(orders=[pending], runtimes=[])
        waiting = self._inspect("2026-09-02T10:00:30")
        self.assertEqual([], waiting["proposals"])
        self.assertEqual("PREVIOUS_TIME_SLICE_EVIDENCE_PENDING", waiting["waiting"][0]["reason"])

    def test_terminal_failure_keeps_later_child_but_uncertainty_requires_review(self) -> None:
        rejected = self._order(1, status="SEND_CALL_REJECTED")
        self._fixture(orders=[rejected], runtimes=[self._runtime(1)])
        after_failure = self._inspect("2026-09-02T10:00:15")
        self.assertEqual(2, after_failure["proposals"][0]["child_sequence_index"])

        uncertain = self._order(1, status="SEND_UNCERTAIN")
        self._fixture(orders=[uncertain], runtimes=[])
        unsafe = self._inspect("2026-09-02T10:00:30")
        self.assertEqual([], unsafe["proposals"])
        self.assertIn("TIME_SLICE_UNSAFE_CHILD", " ".join(unsafe["reviews"][0]["review_reasons"]))

    def test_holding_open_order_and_partial_fill_evidence_prevent_oversell(self) -> None:
        accepted = self._order(1, status="BROKER_ACCEPTED", remaining=4)
        self._fixture(
            orders=[accepted],
            runtimes=[self._runtime(1)],
            holding_quantity=10,
            available_quantity=6,
        )
        with_open = self._inspect("2026-09-02T10:00:15")
        self.assertEqual(3, with_open["proposals"][0]["safe_quantity"])

        partial = self._order(1, status="PARTIALLY_FILLED", remaining=2)
        self._fixture(
            orders=[partial],
            runtimes=[self._runtime(1)],
            fills=[
                {
                    "fill_id": "FILL-TIME-1",
                    "execution_id": "EXEC-TIME-1",
                    "execution_process_id": self.process_id,
                    "filled_quantity": 2,
                    "recorded_at": "2026-09-02T10:00:20",
                }
            ],
            holding_quantity=8,
            available_quantity=6,
            position_quantity=8,
        )
        after_partial = self._inspect("2026-09-02T10:00:15")
        self.assertEqual(3, after_partial["proposals"][0]["safe_quantity"])

        self._fixture(
            orders=[accepted],
            runtimes=[self._runtime(1)],
            holding_quantity=2,
            available_quantity=2,
            position_quantity=2,
        )
        reduced = self._inspect("2026-09-02T10:00:15")
        self.assertEqual(1, reduced["proposals"][0]["safe_quantity"])

    def test_final_missing_child_marks_completion_proposal(self) -> None:
        orders = [self._order(1), self._order(2)]
        self._fixture(orders=orders, runtimes=[self._runtime(1), self._runtime(2)])

        result = self._inspect("2026-09-02T10:00:30")

        self.assertTrue(result["proposals"][0]["complete_after_enqueue"])
        self.assertEqual(3, result["proposals"][0]["child_sequence_index"])

    def test_schedule_is_anchored_to_durable_signal_creation_time(self) -> None:
        intents = self._intents()
        for intent in intents:
            intent["source_signal_id"] = None
            intent["execution_process_id"] = None
            intent["execution_id"] = None
            intent["child_plan"].pop("scheduled_at", None)
            intent["child_plan"].pop("schedule_anchor_at", None)
        fields = {
            "signal": "SELL",
            "execution_intents": intents,
        }
        with mock.patch.object(routine_signal_queue, "QUEUE_PATH", self.signals), mock.patch.object(
            routine_signal_queue, "now_text", return_value="2026-09-02T10:00:00"
        ):
            queued = routine_signal_queue.enqueue_routine_signal(
                fields,
                routine="지표추종매매",
                code=self.code,
                name="삼성전자",
            )
        root = json.loads(self.signals.read_text(encoding="utf-8"))
        record = root["signals"][0]
        materialized = record["execution_intents"]
        scheduled = [
            item["child_plan"]["scheduled_at"]
            for item in materialized
        ]

        self.assertEqual("queued", queued["status"])
        self.assertEqual(
            [
                "2026-09-02T10:00:00.000",
                "2026-09-02T10:00:15.000",
                "2026-09-02T10:00:30.000",
            ],
            scheduled,
        )
        self.assertEqual({record["id"]}, {item["source_signal_id"] for item in materialized})
        self.assertEqual(1, len({item["execution_process_id"] for item in materialized}))
        self.assertEqual(3, len({item["execution_id"] for item in materialized}))
        candidates = order_queue.signal_to_order_candidates(record, 1)
        self.assertEqual(3, len({item["id"] for item in candidates}))

    def test_due_child_reenters_existing_candidate_approval_policy_pipeline(self) -> None:
        self._fixture()
        proposal = self._inspect("2026-09-02T10:00:00")["proposals"][0]
        captured: list[dict[str, object]] = []

        def append(candidates):
            captured.extend(candidates)
            return {
                "ok": True,
                "orders_created": len(candidates),
                "duplicates": 0,
                "order_queue_written": True,
                "created_orders": candidates,
                "duplicate_orders": [],
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
            result = routine_signal_consumer.enqueue_scheduled_time_slice(proposal)

        self.assertTrue(result["ok"])
        self.assertEqual(1, result["orders_created"])
        self.assertEqual(1, result["approved"])
        self.assertEqual(1, result["policy_executable"])
        self.assertEqual(1, len(result["executable_order_ids"]))
        self.assertEqual(1, len(captured))
        self.assertEqual("TIME_SLICE", captured[0]["child_kind"])
        self.assertEqual(self.process_id, captured[0]["execution_process_id"])

    def test_standard_signal_consumer_does_not_expand_future_time_slices(self) -> None:
        signal = {
            "id": self.signal_id,
            "status": "PENDING",
            "execution_intents": self._intents(),
        }
        with mock.patch.object(
            routine_signal_consumer,
            "load_pending_routine_signals",
            return_value=[signal],
        ), mock.patch.object(
            routine_signal_consumer,
            "dry_run_order_manager_for_signal_with_payload_preview",
        ) as preview, mock.patch.object(
            routine_signal_consumer,
            "_build_order_queue_candidates_for_signals",
            return_value={
                "ok": True,
                "orders_created": 0,
                "duplicates": 0,
                "ignored": 0,
                "approval_checked": 0,
                "approved": 0,
                "blocked": 0,
                "policy_checked": 0,
                "policy_executable": 0,
                "policy_blocked": 0,
                "policy_errors": 0,
                "order_queue_written": False,
                "approval_results": [],
                "policy_results": [],
                "executable_order_ids": [],
            },
        ) as enqueue:
            result = routine_signal_consumer.consume_pending_routine_signals_dry_run(
                write_order_queue=True,
                apply_approval=True,
            )

        self.assertEqual(0, result["summary"]["signals_checked"])
        preview.assert_not_called()
        enqueue.assert_called_once_with([], apply_approval=True)

    def test_final_due_child_marks_durable_plan_complete_after_enqueue(self) -> None:
        orders = [self._order(1), self._order(2)]
        self._fixture(orders=orders, runtimes=[self._runtime(1), self._runtime(2)])
        proposal = self._inspect("2026-09-02T10:00:30")["proposals"][0]
        with mock.patch.object(
            routine_signal_consumer,
            "_build_order_queue_candidates_for_signals",
            return_value={
                "ok": True,
                "orders_created": 1,
                "duplicates": 0,
                "executable_order_ids": ["ORDER-TIME-3"],
            },
        ), mock.patch.object(
            routine_signal_consumer,
            "update_signal_status",
            return_value={"ok": True, "after_status": "PREVIEWED"},
        ) as update:
            result = routine_signal_consumer.enqueue_scheduled_time_slice(proposal)

        self.assertTrue(result["ok"])
        update.assert_called_once_with(
            self.signal_id,
            "PREVIEWED",
            metadata={
                "time_slice_plan_complete": True,
                "time_slice_last_child_sequence_index": 3,
            },
        )

    def test_timer_routes_due_child_and_review_to_existing_boundaries(self) -> None:
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
        due = {
            "ok": True,
            "proposals": [{"code": "000660", "execution_intents": [{}], "signal": {}}],
            "reviews": [{"code": "005930", "review_reasons": ["SEND_UNCERTAIN"]}],
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
            "inspect_due_time_slices",
            return_value=due,
        ) as inspect, mock.patch.object(
            gui_auto_trade_timer,
            "enqueue_scheduled_time_slice",
            return_value={
                "ok": True,
                "orders_created": 1,
                "executable_order_ids": ["ORDER-TIME-1"],
            },
        ) as enqueue, mock.patch.object(
            gui_auto_trade_timer,
            "inspect_execution_process_supplements",
            return_value={
                "ok": True,
                "proposals": [],
                "reviews": [],
                "waiting": [],
                "errors": [],
            },
        ), mock.patch.object(
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
            order_ids=["ORDER-TIME-1"],
        )
        self.assertEqual(1, result["time_slice"]["reviews"])
        self.assertEqual(1, result["time_slice"]["orders_created"])


if __name__ == "__main__":
    unittest.main()
