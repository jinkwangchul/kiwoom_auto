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

from execution_ratio_slice_eligibility import _ratio_result, inspect_eligible_ratio_slices
from execution_preview_service import preview_execution_for_order
import gui_auto_trade_timer
import order_queue
import routine_signal_consumer


class ExecutionRatioSliceEligibilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.signals = self.root / "routine_signals.json"
        self.orders = self.root / "order_queue.json"
        self.executions = self.root / "order_executions.json"
        self.fills = self.root / "fills.json"
        self.positions = self.root / "positions.json"
        self.holdings = self.root / "broker_holdings.json"
        self.signal_id = "SIGNAL-RATIO-1"
        self.process_id = "PROCESS-RATIO-1"
        self.code = "005930"

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _write(path: Path, **fields: object) -> None:
        path.write_text(
            json.dumps({"version": 1, **fields}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _intents(
        self,
        *,
        left: str = "ORDER_PRICE",
        right: str = "CURRENT_PRICE",
        direction: str = "UP",
        compare: str = ">=",
        threshold: float = 1.0,
    ) -> list[dict[str, object]]:
        plan = {
            "planned_child_count": 3,
            "configured_child_count": 3,
            "planned_total_quantity": 10,
            "ratio_left": left,
            "ratio_right": right,
            "ratio_direction": direction,
            "ratio_value": threshold,
            "ratio_compare": compare,
            "ratio_unit": "PERCENT",
            "order_price": 10_000,
        }
        values: list[dict[str, object]] = []
        for index, quantity in enumerate((4, 3, 3), start=1):
            values.append(
                {
                    "execution_id": f"EXEC-RATIO-{index}",
                    "side": "SELL",
                    "quantity": quantity,
                    "planned_total_quantity": 10,
                    "price_basis": "ORDER_PRICE",
                    "price": 10_000,
                    "hoga": "LIMIT",
                    "routine_type": "INDICATOR_FOLLOW",
                    "routine_instance_id": "INSTANCE-1",
                    "source_signal_id": self.signal_id,
                    "execution_process_id": self.process_id,
                    "execution_mode": "MULTI_RATIO",
                    "execution_process_owner_required": True,
                    "plan_generation": 0,
                    "child_sequence_index": index,
                    "child_sequence_total": 3,
                    "child_kind": "RATIO_SLICE",
                    "child_plan": {
                        "planned_quantity": quantity,
                        "planned_price": 10_000,
                        "ratio_step_index": index,
                    },
                    "multi_ratio_plan": deepcopy(plan),
                    "ratio_left": left,
                    "ratio_right": right,
                    "ratio_direction": direction,
                    "ratio_value": threshold,
                    "ratio_compare": compare,
                    "ratio_count": 3,
                }
            )
        return values

    def _order(self, index: int, *, status: str = "BROKER_ACCEPTED") -> dict[str, object]:
        intent = self._intents()[index - 1]
        record = {
            "id": f"ORDER-RATIO-{index}",
            "source": "execution_queue_pending",
            "status": status,
            "source_signal_id": self.signal_id,
            "execution_process_id": self.process_id,
            "execution_id": intent["execution_id"],
            "plan_generation": 0,
            "child_sequence_index": index,
            "child_sequence_total": 3,
            "child_kind": "RATIO_SLICE",
            "child_plan": intent["child_plan"],
            "code": self.code,
            "quantity": intent["quantity"],
            "execution_intent": intent,
            "updated_at": "2026-09-02T10:00:05",
        }
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
            "child_kind": "RATIO_SLICE",
            "child_plan": intent["child_plan"],
        }

    def _fixture(
        self,
        *,
        intents: list[dict[str, object]] | None = None,
        orders: list[dict[str, object]] | None = None,
        runtimes: list[dict[str, object]] | None = None,
        fills: list[dict[str, object]] | None = None,
        holding_quantity: int = 10,
        available_quantity: int = 10,
        position_quantity: int = 10,
        average_price: int = 9_500,
    ) -> None:
        plan = intents or self._intents()
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
                    "execution_intent": plan[0],
                    "execution_intents": plan,
                }
            ],
        )
        self._write(self.orders, orders=orders or [])
        self._write(
            self.executions,
            executions=runtimes or [],
            processes=([{"execution_process_id": self.process_id}] if (orders or runtimes) else []),
        )
        self._write(self.fills, fills=fills or [])
        self._write(
            self.positions,
            positions=[
                {
                    "account_no": "12345678",
                    "code": self.code,
                    "quantity": position_quantity,
                    "average_price": average_price,
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
                    "received_at": "2026-09-02T10:01:00",
                    "reconciliation_status": "CONSISTENT",
                    "manual_reconciliation_required": False,
                }
            ],
        )

    def _inspect(self, *, current_price: float | None) -> dict[str, object]:
        prices = {} if current_price is None else {self.code: current_price}
        return inspect_eligible_ratio_slices(
            selected_account_no="12345678",
            actionable_prices_by_code=prices,
            allowed_stock_codes=(self.code,),
            now=datetime.fromisoformat("2026-09-02T10:01:00"),
            signals_path=self.signals,
            orders_path=self.orders,
            executions_path=self.executions,
            fills_path=self.fills,
            positions_path=self.positions,
            holdings_path=self.holdings,
        )

    def test_threshold_below_exact_and_above_use_percentage_points(self) -> None:
        self._fixture()

        below = self._inspect(current_price=10_099)
        exact = self._inspect(current_price=10_100)
        above = self._inspect(current_price=10_101)

        self.assertEqual([], below["proposals"])
        self.assertEqual("RATIO_THRESHOLD_NOT_MET", below["waiting"][0]["reason"])
        self.assertEqual(1, exact["proposals"][0]["child_sequence_index"])
        self.assertEqual(4, exact["proposals"][0]["safe_quantity"])
        self.assertEqual(1, above["proposals"][0]["observed_percent"] // 1)

    def test_direction_and_compare_boundaries_are_exact(self) -> None:
        self.assertEqual((True, 1.0), _ratio_result(left=10_000, right=9_900, direction="DOWN", compare=">=", threshold=1.0))
        self.assertEqual((True, 1.0), _ratio_result(left=10_000, right=10_100, direction="BOTH", compare="WITHIN", threshold=1.0))
        self.assertEqual((False, 1.0), _ratio_result(left=10_000, right=10_100, direction="BOTH", compare="OUTSIDE", threshold=1.0))

    def test_current_price_missing_waits_and_average_price_is_authoritative_position(self) -> None:
        self._fixture()
        missing = self._inspect(current_price=None)
        self.assertEqual("RATIO_CURRENT_PRICE_UNAVAILABLE", missing["waiting"][0]["reason"])

        avg_intents = self._intents(left="AVG_PRICE", right="CURRENT_PRICE", threshold=1.0)
        self._fixture(intents=avg_intents, average_price=10_000)
        eligible = self._inspect(current_price=10_100)
        self.assertEqual(1, eligible["proposals"][0]["child_sequence_index"])

    def test_existing_child_progresses_once_and_pending_child_waits(self) -> None:
        first = self._order(1)
        self._fixture(orders=[first], runtimes=[self._runtime(1)])
        second = self._inspect(current_price=10_100)
        replay = self._inspect(current_price=10_100)
        self.assertEqual(2, second["proposals"][0]["child_sequence_index"])
        self.assertEqual(2, replay["proposals"][0]["child_sequence_index"])

        self._fixture(orders=[self._order(1, status="ORDER_QUEUED")])
        waiting = self._inspect(current_price=10_100)
        self.assertEqual([], waiting["proposals"])
        self.assertEqual("PREVIOUS_RATIO_SLICE_EVIDENCE_PENDING", waiting["waiting"][0]["reason"])

    def test_reject_continues_but_send_uncertain_is_reviewed(self) -> None:
        self._fixture(
            orders=[self._order(1, status="SEND_CALL_REJECTED")],
            runtimes=[self._runtime(1)],
        )
        continued = self._inspect(current_price=10_100)
        self.assertEqual(2, continued["proposals"][0]["child_sequence_index"])

        self._fixture(orders=[self._order(1, status="SEND_UNCERTAIN")])
        uncertain = self._inspect(current_price=10_100)
        self.assertEqual([], uncertain["proposals"])
        self.assertIn("RATIO_SLICE_UNSAFE_CHILD", " ".join(uncertain["reviews"][0]["review_reasons"]))

    def test_runtime_identity_mismatch_is_reviewed(self) -> None:
        runtime = self._runtime(1)
        runtime["child_sequence_index"] = 2
        self._fixture(orders=[self._order(1)], runtimes=[runtime])

        result = self._inspect(current_price=10_100)

        self.assertEqual([], result["proposals"])
        self.assertIn("RATIO_SLICE_RUNTIME_IDENTITY_MISMATCH", " ".join(result["reviews"][0]["review_reasons"]))

    def test_latest_holding_open_order_and_partial_fill_prevent_oversell(self) -> None:
        first = self._order(1)
        self._fixture(
            orders=[first],
            runtimes=[self._runtime(1)],
            available_quantity=2,
            holding_quantity=2,
            position_quantity=2,
        )
        reduced = self._inspect(current_price=10_100)
        self.assertEqual(1, reduced["proposals"][0]["safe_quantity"])

        partial = self._order(1, status="PARTIALLY_FILLED")
        self._fixture(
            orders=[partial],
            runtimes=[self._runtime(1)],
            fills=[
                {
                    "fill_id": "FILL-RATIO-1",
                    "execution_id": "EXEC-RATIO-1",
                    "filled_quantity": 2,
                    "recorded_at": "2026-09-02T10:00:20",
                }
            ],
            holding_quantity=8,
            available_quantity=6,
            position_quantity=8,
        )
        after_partial = self._inspect(current_price=10_100)
        self.assertEqual(3, after_partial["proposals"][0]["safe_quantity"])

    def test_identity_materialization_and_order_ids_are_unique(self) -> None:
        intents = self._intents()
        from execution_provenance_contract import materialize_execution_intent_children

        materialized = materialize_execution_intent_children(
            intents,
            source_signal_id=self.signal_id,
            execution_process_id=self.process_id,
        )
        for intent in materialized:
            intent["provenance_approved_at"] = "2026-09-02T10:00:00+09:00"
        signal = {
            "id": self.signal_id,
            "routine": "지표추종매매",
            "routine_instance_id": "INSTANCE-1",
            "code": self.code,
            "name": "삼성전자",
            "signal": "SELL",
            "status": "PENDING",
            "execution_intent": materialized[0],
            "execution_intents": materialized,
        }
        candidates = order_queue.signal_to_order_candidates(signal, 1)
        self.assertEqual({self.signal_id}, {item["source_signal_id"] for item in materialized})
        self.assertEqual(1, len({item["execution_process_id"] for item in materialized}))
        self.assertEqual(3, len({item["execution_id"] for item in materialized}))
        self.assertEqual(3, len({item["id"] for item in candidates}))
        previews = []
        for candidate in candidates:
            candidate["status"] = "REAL_READY"
            candidate["execution_enabled"] = True
            previews.append(
                preview_execution_for_order(
                    candidate,
                    {
                        "operator_confirmed": True,
                        "real_trade_enabled": True,
                        "account_no": "12345678",
                    },
                )["candidate_result"]
            )
        self.assertEqual(3, len({item["request_hash_preview"] for item in previews}))
        self.assertEqual(3, len({item["lock_preview"]["lock_id"] for item in previews}))

    def test_eligible_child_uses_generic_candidate_approval_policy_pipeline(self) -> None:
        self._fixture()
        proposal = self._inspect(current_price=10_100)["proposals"][0]
        captured: list[dict[str, object]] = []

        def append(candidates):
            captured.extend(candidates)
            return {"ok": True, "orders_created": 1, "duplicates": 0, "order_queue_written": True, "created_orders": candidates, "duplicate_orders": []}

        with mock.patch.object(routine_signal_consumer, "routine_execution_intent_admission", return_value={"allowed": True}), mock.patch.object(
            routine_signal_consumer, "read_order_queue", return_value={"orders": []}
        ), mock.patch.object(routine_signal_consumer, "append_order_candidates", side_effect=append), mock.patch.object(
            routine_signal_consumer, "evaluate_order_approval", return_value={"approval_status": "APPROVED", "approval_reason": ""}
        ), mock.patch.object(
            routine_signal_consumer.operation_policy_gate,
            "apply_operation_policy_gate_for_order",
            return_value={"ok": True, "status": "allowed", "after_status": "EXECUTABLE", "policy_status": "EXECUTABLE", "reason": ""},
        ):
            result = routine_signal_consumer.enqueue_eligible_ratio_slice(proposal)

        self.assertTrue(result["ok"], result)
        self.assertEqual(1, result["orders_created"])
        self.assertEqual(1, result["policy_executable"])
        self.assertEqual("RATIO_SLICE", captured[0]["child_kind"])

    def test_standard_consumer_does_not_expand_ratio_plan(self) -> None:
        signal = {"id": self.signal_id, "status": "PENDING", "execution_intents": self._intents()}
        empty = {
            "ok": True, "orders_created": 0, "duplicates": 0, "ignored": 0,
            "approval_checked": 0, "approved": 0, "blocked": 0,
            "policy_checked": 0, "policy_executable": 0, "policy_blocked": 0,
            "policy_errors": 0, "order_queue_written": False,
            "approval_results": [], "policy_results": [], "executable_order_ids": [],
        }
        with mock.patch.object(routine_signal_consumer, "load_pending_routine_signals", return_value=[signal]), mock.patch.object(
            routine_signal_consumer, "_build_order_queue_candidates_for_signals", return_value=empty
        ) as enqueue:
            result = routine_signal_consumer.consume_pending_routine_signals_dry_run(write_order_queue=True)
        self.assertEqual(0, result["summary"]["signals_checked"])
        enqueue.assert_called_once_with([], apply_approval=False)

    def test_final_ratio_child_marks_durable_plan_complete_after_enqueue(self) -> None:
        self._fixture(
            orders=[self._order(1), self._order(2)],
            runtimes=[self._runtime(1), self._runtime(2)],
        )
        proposal = self._inspect(current_price=10_100)["proposals"][0]
        with mock.patch.object(
            routine_signal_consumer,
            "_build_order_queue_candidates_for_signals",
            return_value={"ok": True, "orders_created": 1, "executable_order_ids": ["ORDER-RATIO-3"]},
        ), mock.patch.object(
            routine_signal_consumer,
            "update_signal_status",
            return_value={"ok": True, "after_status": "PREVIEWED"},
        ) as update:
            result = routine_signal_consumer.enqueue_eligible_ratio_slice(proposal)

        self.assertTrue(result["ok"])
        update.assert_called_once_with(
            self.signal_id,
            "PREVIEWED",
            metadata={
                "ratio_slice_plan_complete": True,
                "ratio_slice_last_child_sequence_index": 3,
            },
        )

    def test_timer_isolates_review_and_executes_other_stock(self) -> None:
        window = SimpleNamespace(
            _selected_account_no=lambda: "12345678",
            fresh_monitoring_market_information_state=lambda code: SimpleNamespace(last_price=10_100),
            mark_review_required=mock.Mock(return_value=True),
            statusBarMessage=mock.Mock(),
            auto_process_executable_orders_for_real_trade=mock.Mock(return_value={"processed": 1, "blocked": 0}),
        )
        snapshot = SimpleNamespace(entries=(
            SimpleNamespace(execution_ready=True, real_trade_enabled=True, signal_probe_only=False, stock_code="005930", stock_name="삼성전자", stock_dir=self.root / "005930"),
            SimpleNamespace(execution_ready=True, real_trade_enabled=True, signal_probe_only=False, stock_code="000660", stock_name="SK하이닉스", stock_dir=self.root / "000660"),
        ))
        inspected = {
            "ok": True,
            "proposals": [{"code": "000660", "execution_intents": [{}], "signal": {}}],
            "reviews": [{"code": "005930", "review_reasons": ["SEND_UNCERTAIN"]}],
            "waiting": [], "errors": [],
        }
        summary = {"signals_checked": 0, "blocked": 0, "allowed": 0, "errors": 0, "orders_created": 0, "approval_checked": 0, "approved": 0, "executable_order_ids": []}
        empty_inspection = {"ok": True, "proposals": [], "reviews": [], "waiting": [], "errors": []}
        with mock.patch.object(gui_auto_trade_timer, "inspect_eligible_ratio_slices", return_value=inspected), mock.patch.object(
            gui_auto_trade_timer, "enqueue_eligible_ratio_slice", return_value={"ok": True, "orders_created": 1, "executable_order_ids": ["ORDER-RATIO-1"]}
        ), mock.patch.object(gui_auto_trade_timer, "inspect_due_time_slices", return_value=empty_inspection), mock.patch.object(
            gui_auto_trade_timer, "inspect_execution_process_supplements", return_value=empty_inspection
        ), mock.patch.object(gui_auto_trade_timer, "consume_pending_routine_signals_dry_run", return_value={"summary": summary}), mock.patch.object(
            gui_auto_trade_timer, "auto_trade_signal_probe_only_active", return_value=False
        ), mock.patch.object(gui_auto_trade_timer, "auto_trade_real_execution_active", return_value=True):
            result = gui_auto_trade_timer._process_pending_signal_pipeline(window, snapshot)

        window.mark_review_required.assert_called_once()
        window.auto_process_executable_orders_for_real_trade.assert_called_once_with(limit=5, order_ids=["ORDER-RATIO-1"])
        self.assertEqual(1, result["ratio_slice"]["reviews"])
        self.assertEqual(1, result["ratio_slice"]["orders_created"])


if __name__ == "__main__":
    unittest.main()
