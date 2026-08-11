# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import operation_policy_gate


class OperationPolicyGateSingleOrderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.runtime_dir = self.root / "runtime"
        self.stocks_dir = self.root / "stocks"
        self.order_queue_path = self.runtime_dir / "order_queue.json"
        self.operation_state_path = self.runtime_dir / "operation_state.json"
        self.stock_dir = self.stocks_dir / "003550_LG"
        self.stock_dir.mkdir(parents=True)
        self.runtime_dir.mkdir(parents=True)
        self._patches = [
            patch.object(operation_policy_gate, "RUNTIME_DIR", self.runtime_dir),
            patch.object(operation_policy_gate, "STOCKS_DIR", self.stocks_dir),
            patch.object(operation_policy_gate, "ORDER_QUEUE_PATH", self.order_queue_path),
            patch.object(operation_policy_gate, "OPERATION_STATE_PATH", self.operation_state_path),
        ]
        for item in self._patches:
            item.start()
        self._write_json(self.operation_state_path, {})

    def tearDown(self) -> None:
        for item in reversed(self._patches):
            item.stop()
        self.tmp.cleanup()

    def _write_json(self, path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_queue(self) -> dict:
        return json.loads(self.order_queue_path.read_text(encoding="utf-8"))

    def _write_state(self, **extra: object) -> None:
        state = {
            "status": "MONITORING",
            "trade_enabled": True,
            "real_trade_enabled": False,
            "buy_enabled": False,
            "sell_enabled": False,
        }
        state.update(extra)
        self._write_json(self.stock_dir / "state.json", state)

    def _order(self, status: str = "APPROVED", order_id: str = "ORDER_1") -> dict:
        return {
            "id": order_id,
            "status": status,
            "approval_status": "APPROVED" if status == "APPROVED" else "",
            "code": "003550",
            "name": "LG",
            "side": "SELL",
            "candidate_status": "CANDIDATE_READY",
            "quantity": 10,
            "price": 100.0,
            "execution_enabled": False,
        }

    def _write_queue(
        self,
        status: str = "APPROVED",
        order_id: str = "ORDER_1",
        *,
        revision: int | None = None,
        orders: list[dict] | None = None,
    ) -> None:
        data = {
            "version": 1,
            "updated_at": "",
            "orders": orders if orders is not None else [self._order(status=status, order_id=order_id)],
        }
        if revision is not None:
            data["revision"] = revision
        self._write_json(
            self.order_queue_path,
            data,
        )

    def _single_order(self) -> dict:
        orders = self._read_queue().get("orders", [])
        self.assertEqual(1, len(orders))
        return orders[0]

    def test_approved_order_promotes_to_executable(self) -> None:
        self._write_state()
        self._write_queue(status="APPROVED")

        result = operation_policy_gate.apply_operation_policy_gate_for_order(
            "ORDER_1",
            queue_path=self.order_queue_path,
        )
        order = self._single_order()

        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertTrue(result["committed"])
        self.assertTrue(result["queue_committed"])
        self.assertEqual("EXECUTABLE", result["after_status"])
        self.assertEqual("EXECUTABLE", order.get("status"))
        self.assertEqual("EXECUTABLE", order.get("policy_status"))
        self.assertFalse(order.get("execution_enabled"))
        self.assertEqual(1, self._read_queue().get("revision"))

    def test_approved_order_blocked_by_policy(self) -> None:
        self._write_state(review_required=True)
        self._write_queue(status="APPROVED")

        result = operation_policy_gate.apply_operation_policy_gate_for_order(
            "ORDER_1",
            queue_path=self.order_queue_path,
        )
        order = self._single_order()

        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertTrue(result["committed"])
        self.assertEqual("BLOCKED_POLICY", result["after_status"])
        self.assertEqual("BLOCKED_POLICY", order.get("status"))
        self.assertEqual("BLOCKED_POLICY", order.get("policy_status"))
        self.assertFalse(order.get("execution_enabled"))

    def test_early_close_routine_allows_buy_and_sell_before_final_sell(self) -> None:
        self._write_state(
            status="EARLY_CLOSE",
            trade_enabled=True,
            buy_enabled=True,
            sell_enabled=True,
            early_close_requested_at="2026-08-10 10:00:00",
            early_close_method="루틴",
            close_routine_final_sell_ordered=False,
        )

        for side in ("BUY", "SELL"):
            order = self._order()
            order["side"] = side
            result = operation_policy_gate.evaluate_operation_policy(order)
            self.assertEqual("EXECUTABLE", result["policy_status"], side)

    def test_early_close_routine_blocks_buy_and_sell_after_final_sell(self) -> None:
        self._write_state(
            status="EARLY_CLOSE",
            trade_enabled=True,
            buy_enabled=False,
            sell_enabled=False,
            early_close_requested_at="2026-08-10 10:00:00",
            early_close_method="루틴",
            close_routine_final_sell_ordered=True,
            close_routine_final_sell_ordered_at="2026-08-10 10:15:00",
        )

        for side in ("BUY", "SELL"):
            order = self._order()
            order["side"] = side
            result = operation_policy_gate.evaluate_operation_policy(order)
            self.assertEqual("BLOCKED_POLICY", result["policy_status"], side)

    def test_auto_close_routine_uses_same_before_and_after_final_sell_contract(self) -> None:
        base_state = {
            "status": "AUTO_CLOSE",
            "trade_enabled": True,
            "buy_enabled": True,
            "sell_enabled": True,
            "auto_close_requested_at": "2026-08-10 15:20:00",
            "auto_close_method": "루틴매도신호",
        }
        self._write_state(**base_state, close_routine_final_sell_ordered=False)
        buy_order = self._order()
        buy_order["side"] = "BUY"
        self.assertEqual(
            "EXECUTABLE",
            operation_policy_gate.evaluate_operation_policy(buy_order)["policy_status"],
        )

        self._write_state(
            **base_state,
            close_routine_final_sell_ordered=True,
            close_routine_final_sell_ordered_at="2026-08-10 15:25:00",
        )
        self.assertEqual(
            "BLOCKED_POLICY",
            operation_policy_gate.evaluate_operation_policy(buy_order)["policy_status"],
        )

    def test_global_emergency_stop_writer_blocks_operation_policy_gate(self) -> None:
        self._write_state()
        self._write_queue(status="APPROVED")
        self._write_json(self.operation_state_path, {"existing_key": "preserve"})

        writer_result = operation_policy_gate.write_global_emergency_stop_state(
            emergency_stop=True,
            timestamp="2026-07-29 10:00:00",
        )
        result = operation_policy_gate.apply_operation_policy_gate_for_order(
            "ORDER_1",
            queue_path=self.order_queue_path,
        )
        order = self._single_order()
        operation_state = json.loads(self.operation_state_path.read_text(encoding="utf-8"))

        self.assertTrue(writer_result["ok"])
        self.assertTrue(operation_state["emergency_stop"])
        self.assertEqual("2026-07-29 10:00:00", operation_state["emergency_stopped_at"])
        self.assertEqual("USER_EMERGENCY_STOP", operation_state["emergency_reason"])
        self.assertEqual("CONTROL_WINDOW", operation_state["emergency_source"])
        self.assertEqual("preserve", operation_state["existing_key"])
        self.assertEqual("BLOCKED_POLICY", result["after_status"])
        self.assertEqual("BLOCKED_POLICY", order.get("status"))
        self.assertEqual("긴급정지 활성", order.get("policy_reason"))

    def test_global_emergency_release_writer_restores_operation_policy_contract(self) -> None:
        self._write_state()
        self._write_queue(status="APPROVED")
        self._write_json(
            self.operation_state_path,
            {
                "existing_key": "preserve",
                "emergency_stop": True,
                "emergency_stopped_at": "2026-07-29 10:00:00",
            },
        )

        writer_result = operation_policy_gate.write_global_emergency_stop_state(
            emergency_stop=False,
            timestamp="2026-07-29 10:05:00",
        )
        result = operation_policy_gate.apply_operation_policy_gate_for_order(
            "ORDER_1",
            queue_path=self.order_queue_path,
        )
        order = self._single_order()
        operation_state = json.loads(self.operation_state_path.read_text(encoding="utf-8"))

        self.assertTrue(writer_result["ok"])
        self.assertFalse(operation_state["emergency_stop"])
        self.assertEqual("2026-07-29 10:00:00", operation_state["emergency_stopped_at"])
        self.assertEqual("2026-07-29 10:05:00", operation_state["emergency_released_at"])
        self.assertEqual("", operation_state["emergency_reason"])
        self.assertEqual("", operation_state["emergency_source"])
        self.assertEqual("preserve", operation_state["existing_key"])
        self.assertEqual("EXECUTABLE", result["after_status"])
        self.assertEqual("EXECUTABLE", order.get("status"))

    def test_global_operation_running_writer_records_today_start_and_preserves_existing_keys(self) -> None:
        self._write_json(
            self.operation_state_path,
            {
                "existing_key": "preserve",
                "emergency_stop": False,
                "emergency_released_at": "2026-07-29 09:00:00",
            },
        )

        writer_result = operation_policy_gate.write_global_operation_running_state(
            participant_stock_codes=["005930"],
            timestamp="2026-07-29 09:05:00",
        )
        operation_state = json.loads(self.operation_state_path.read_text(encoding="utf-8"))

        self.assertTrue(writer_result["ok"])
        self.assertEqual("2026-07-29", operation_state["operation_date"])
        self.assertEqual("RUNNING", operation_state["operation_status"])
        self.assertEqual("2026-07-29 09:05:00", operation_state["operation_started_at"])
        self.assertEqual("2026-07-29 09:05:00", operation_state["operation_updated_at"])
        self.assertEqual(["005930"], operation_state["operation_participant_stock_codes"])
        self.assertFalse(operation_state["emergency_stop"])
        self.assertEqual("2026-07-29 09:00:00", operation_state["emergency_released_at"])
        self.assertEqual("preserve", operation_state["existing_key"])

    def test_global_operation_running_writer_preserves_first_start_for_same_day_running(self) -> None:
        self._write_json(
            self.operation_state_path,
            {
                "operation_date": "2026-07-29",
                "operation_status": "RUNNING",
                "operation_started_at": "2026-07-29 09:05:00",
                "operation_updated_at": "2026-07-29 09:05:00",
                "operation_participant_stock_codes": ["005930"],
            },
        )

        operation_policy_gate.write_global_operation_running_state(
            participant_stock_codes=["000660", "005930"],
            timestamp="2026-07-29 10:15:00",
        )
        operation_state = json.loads(self.operation_state_path.read_text(encoding="utf-8"))

        self.assertEqual("2026-07-29 09:05:00", operation_state["operation_started_at"])
        self.assertEqual("2026-07-29 10:15:00", operation_state["operation_updated_at"])
        self.assertEqual(["000660", "005930"], operation_state["operation_participant_stock_codes"])

    def test_global_operation_running_writer_resets_start_when_date_changes(self) -> None:
        self._write_json(
            self.operation_state_path,
            {
                "operation_date": "2026-07-28",
                "operation_status": "RUNNING",
                "operation_started_at": "2026-07-28 09:05:00",
                "operation_updated_at": "2026-07-28 09:05:00",
                "operation_participant_stock_codes": ["000660"],
            },
        )

        operation_policy_gate.write_global_operation_running_state(
            participant_stock_codes=["005930"],
            timestamp="2026-07-29 09:10:00",
        )
        operation_state = json.loads(self.operation_state_path.read_text(encoding="utf-8"))

        self.assertEqual("2026-07-29", operation_state["operation_date"])
        self.assertEqual("RUNNING", operation_state["operation_status"])
        self.assertEqual("2026-07-29 09:10:00", operation_state["operation_started_at"])
        self.assertEqual("2026-07-29 09:10:00", operation_state["operation_updated_at"])
        self.assertEqual(["005930"], operation_state["operation_participant_stock_codes"])

    def test_global_operation_running_writer_clears_previous_normal_end_fields_on_new_day(self) -> None:
        self._write_json(
            self.operation_state_path,
            {
                "operation_date": "2026-07-28",
                "operation_status": "NORMAL_ENDED",
                "operation_started_at": "2026-07-28 09:05:00",
                "operation_updated_at": "2026-07-28 15:31:00",
                "operation_closing_started_at": "2026-07-28 15:20:00",
                "operation_close_reason": "AUTO_CLOSE",
                "operation_ended_at": "2026-07-28 15:31:00",
                "operation_end_reason": "ALL_PARTICIPANTS_COMPLETE",
                "operation_participant_stock_codes": ["000660"],
                "emergency_stop": True,
                "emergency_stopped_at": "2026-07-28 14:00:00",
                "emergency_reason": "USER_EMERGENCY_STOP",
                "emergency_source": "CONTROL_WINDOW",
                "unknown_key": "preserve",
            },
        )

        operation_policy_gate.write_global_operation_running_state(
            participant_stock_codes=["005930"],
            timestamp="2026-07-29 09:10:00",
        )
        operation_state = json.loads(self.operation_state_path.read_text(encoding="utf-8"))

        self.assertEqual("2026-07-29", operation_state["operation_date"])
        self.assertEqual("RUNNING", operation_state["operation_status"])
        self.assertEqual("2026-07-29 09:10:00", operation_state["operation_started_at"])
        self.assertEqual("2026-07-29 09:10:00", operation_state["operation_updated_at"])
        self.assertEqual(["005930"], operation_state["operation_participant_stock_codes"])
        self.assertNotIn("operation_closing_started_at", operation_state)
        self.assertNotIn("operation_close_reason", operation_state)
        self.assertNotIn("operation_ended_at", operation_state)
        self.assertNotIn("operation_end_reason", operation_state)
        self.assertTrue(operation_state["emergency_stop"])
        self.assertEqual("2026-07-28 14:00:00", operation_state["emergency_stopped_at"])
        self.assertEqual("USER_EMERGENCY_STOP", operation_state["emergency_reason"])
        self.assertEqual("CONTROL_WINDOW", operation_state["emergency_source"])
        self.assertEqual("preserve", operation_state["unknown_key"])

    def test_global_operation_running_writer_clears_previous_closing_fields_on_new_day(self) -> None:
        self._write_json(
            self.operation_state_path,
            {
                "operation_date": "2026-07-28",
                "operation_status": "CLOSING",
                "operation_started_at": "2026-07-28 09:05:00",
                "operation_closing_started_at": "2026-07-28 15:20:00",
                "operation_close_reason": "EARLY_CLOSE",
                "operation_ended_at": "2026-07-28 15:31:00",
                "operation_end_reason": "ALL_PARTICIPANTS_COMPLETE",
                "operation_participant_stock_codes": ["000660"],
            },
        )

        operation_policy_gate.write_global_operation_running_state(
            participant_stock_codes=["005930"],
            timestamp="2026-07-29 09:10:00",
        )
        operation_state = json.loads(self.operation_state_path.read_text(encoding="utf-8"))

        self.assertEqual("RUNNING", operation_state["operation_status"])
        self.assertEqual(["005930"], operation_state["operation_participant_stock_codes"])
        for key in operation_policy_gate.PREVIOUS_CLOSE_SESSION_FIELDS:
            self.assertNotIn(key, operation_state)

    def test_global_operation_running_writer_preserves_today_running_close_fields(self) -> None:
        self._write_json(
            self.operation_state_path,
            {
                "operation_date": "2026-07-29",
                "operation_status": "RUNNING",
                "operation_started_at": "2026-07-29 09:05:00",
                "operation_updated_at": "2026-07-29 09:05:00",
                "operation_closing_started_at": "bad-same-day-residue",
                "operation_close_reason": "bad-same-day-residue",
                "operation_ended_at": "bad-same-day-residue",
                "operation_end_reason": "bad-same-day-residue",
                "operation_participant_stock_codes": ["005930"],
            },
        )

        operation_policy_gate.write_global_operation_running_state(
            participant_stock_codes=["000660"],
            timestamp="2026-07-29 10:15:00",
        )
        operation_state = json.loads(self.operation_state_path.read_text(encoding="utf-8"))

        self.assertEqual("2026-07-29 09:05:00", operation_state["operation_started_at"])
        self.assertEqual(["000660", "005930"], operation_state["operation_participant_stock_codes"])
        for key in operation_policy_gate.PREVIOUS_CLOSE_SESSION_FIELDS:
            self.assertEqual("bad-same-day-residue", operation_state[key])

    def test_global_operation_running_writer_ignores_invalid_existing_participants(self) -> None:
        self._write_json(
            self.operation_state_path,
            {
                "operation_date": "2026-07-29",
                "operation_status": "RUNNING",
                "operation_started_at": "2026-07-29 09:05:00",
                "operation_updated_at": "2026-07-29 09:05:00",
                "operation_participant_stock_codes": ["005930", "005930", "", "invalid"],
            },
        )

        operation_policy_gate.write_global_operation_running_state(
            participant_stock_codes=["A000660", "bad", "", "005930"],
            timestamp="2026-07-29 10:15:00",
        )
        operation_state = json.loads(self.operation_state_path.read_text(encoding="utf-8"))

        self.assertEqual(["000660", "005930"], operation_state["operation_participant_stock_codes"])

    def test_global_operation_running_writer_treats_non_list_existing_participants_as_empty(self) -> None:
        self._write_json(
            self.operation_state_path,
            {
                "operation_date": "2026-07-29",
                "operation_status": "RUNNING",
                "operation_started_at": "2026-07-29 09:05:00",
                "operation_participant_stock_codes": "005930",
            },
        )

        operation_policy_gate.write_global_operation_running_state(
            participant_stock_codes=["000660"],
            timestamp="2026-07-29 10:15:00",
        )
        operation_state = json.loads(self.operation_state_path.read_text(encoding="utf-8"))

        self.assertEqual(["000660"], operation_state["operation_participant_stock_codes"])

    def test_non_approved_order_is_skipped(self) -> None:
        self._write_state()
        self._write_queue(status="PENDING")

        result = operation_policy_gate.apply_operation_policy_gate_for_order(
            "ORDER_1",
            queue_path=self.order_queue_path,
        )
        order = self._single_order()

        self.assertFalse(result["ok"])
        self.assertFalse(result["changed"])
        self.assertEqual("blocked", result["status"])
        self.assertEqual("PENDING", order.get("status"))
        self.assertFalse(order.get("execution_enabled"))

    def test_missing_order_id_is_not_found(self) -> None:
        self._write_state()
        self._write_queue(status="APPROVED")

        result = operation_policy_gate.apply_operation_policy_gate_for_order(
            "ORDER_MISSING",
            queue_path=self.order_queue_path,
        )
        order = self._single_order()

        self.assertFalse(result["ok"])
        self.assertFalse(result["changed"])
        self.assertEqual("not_found", result["status"])
        self.assertEqual("APPROVED", order.get("status"))
        self.assertFalse(order.get("execution_enabled"))

    def test_duplicate_order_id_is_blocked_without_write(self) -> None:
        self._write_state()
        self._write_queue(orders=[self._order(order_id="ORDER_1"), self._order(order_id="ORDER_1")])

        result = operation_policy_gate.apply_operation_policy_gate_for_order(
            "ORDER_1",
            queue_path=self.order_queue_path,
        )
        queue = self._read_queue()

        self.assertFalse(result["ok"])
        self.assertFalse(result["changed"])
        self.assertFalse(result["committed"])
        self.assertEqual("duplicate_identity", result["status"])
        self.assertNotIn("revision", queue)
        self.assertEqual(["APPROVED", "APPROVED"], [order["status"] for order in queue["orders"]])

    def test_stale_expected_revision_is_blocked_without_write(self) -> None:
        self._write_state()
        self._write_queue(status="APPROVED", revision=2)

        result = operation_policy_gate.apply_operation_policy_gate_for_order(
            "ORDER_1",
            queue_path=self.order_queue_path,
            expected_revision=1,
        )
        queue = self._read_queue()

        self.assertFalse(result["committed"])
        self.assertFalse(result["changed"])
        self.assertTrue(result["cas_checked"])
        self.assertEqual(2, queue.get("revision"))
        self.assertEqual("APPROVED", queue["orders"][0]["status"])

    def test_same_result_reapply_is_noop(self) -> None:
        self._write_state()
        order = self._order(status="EXECUTABLE")
        order["policy_status"] = "EXECUTABLE"
        self._write_queue(orders=[order], revision=3)

        result = operation_policy_gate.apply_operation_policy_gate_for_order(
            "ORDER_1",
            queue_path=self.order_queue_path,
        )
        queue = self._read_queue()

        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        self.assertFalse(result["committed"])
        self.assertEqual("noop", result["status"])
        self.assertEqual(3, queue.get("revision"))
        self.assertEqual("EXECUTABLE", queue["orders"][0]["status"])

    def test_different_records_can_mutate_concurrently_without_loss(self) -> None:
        self._write_state()
        self._write_queue(orders=[self._order(order_id="ORDER_1"), self._order(order_id="ORDER_2")])
        results: list[dict] = []

        def worker(order_id: str) -> None:
            results.append(
                operation_policy_gate.apply_operation_policy_gate_for_order(
                    order_id,
                    queue_path=self.order_queue_path,
                )
            )

        threads = [threading.Thread(target=worker, args=(order_id,)) for order_id in ("ORDER_1", "ORDER_2")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        queue = self._read_queue()

        self.assertEqual(2, len(results))
        self.assertEqual(2, sum(1 for result in results if result.get("committed")))
        self.assertEqual(2, queue.get("revision"))
        self.assertEqual(["EXECUTABLE", "EXECUTABLE"], [order["status"] for order in queue["orders"]])

    def test_same_record_concurrent_mutation_commits_once(self) -> None:
        self._write_state()
        self._write_queue(status="APPROVED")
        results: list[dict] = []

        def worker() -> None:
            results.append(
                operation_policy_gate.apply_operation_policy_gate_for_order(
                    "ORDER_1",
                    queue_path=self.order_queue_path,
                )
            )

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        queue = self._read_queue()

        self.assertEqual(2, len(results))
        self.assertEqual(1, sum(1 for result in results if result.get("committed")))
        self.assertEqual(1, queue.get("revision"))
        self.assertEqual("EXECUTABLE", queue["orders"][0]["status"])

    def test_operation_policy_gate_has_no_direct_snapshot_writer(self) -> None:
        source = Path(operation_policy_gate.__file__).read_text(encoding="utf-8")

        self.assertNotIn("write_text", source)
        self.assertNotIn("json.dump", source)
        self.assertNotIn("write_order_queue(", source)
        self.assertNotIn("_write_order_queue", source)


if __name__ == "__main__":
    unittest.main()
