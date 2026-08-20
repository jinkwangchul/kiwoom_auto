from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from account_auto_trade_budget_consumption import (
    project_account_auto_trade_budget_consumption,
    project_system_total_budget_buy_admission,
)
from buy_execution_policy import evaluate_buy_execution_policy
import operation_policy_gate


ACCOUNT = "12345678"


class AccountTotalBudgetProjectionTests(unittest.TestCase):
    def test_strict_total_boundary_and_buffer_independence(self) -> None:
        equal = project_system_total_budget_buy_admission(
            total_budget=4_000,
            account_consumed_amount=3_900,
            candidate_buy_amount=100,
        )
        exceeded = project_system_total_budget_buy_admission(
            total_budget=4_000,
            account_consumed_amount=3_901,
            candidate_buy_amount=100,
        )
        buffer_zone = project_system_total_budget_buy_admission(
            total_budget=4_000,
            account_consumed_amount=3_500,
            candidate_buy_amount=200,
        )
        at_total = project_system_total_budget_buy_admission(
            total_budget=4_000,
            account_consumed_amount=4_000,
            candidate_buy_amount=1,
        )
        already_over = project_system_total_budget_buy_admission(
            total_budget=4_000,
            account_consumed_amount=4_001,
            candidate_buy_amount=1,
        )

        self.assertTrue(equal["admitted"])
        self.assertEqual(4_000, equal["projected_account_consumption"])
        self.assertFalse(exceeded["admitted"])
        self.assertEqual("SYSTEM_TOTAL_BUDGET_EXCEEDED", exceeded["reason_code"])
        self.assertTrue(buffer_zone["admitted"])
        self.assertEqual(3_700, buffer_zone["projected_account_consumption"])
        self.assertFalse(at_total["admitted"])
        self.assertEqual(4_001, at_total["projected_account_consumption"])
        self.assertFalse(already_over["admitted"])

    def test_missing_or_malformed_admission_evidence_fails_closed(self) -> None:
        for values in (
            (None, 0, 1),
            (4_000, None, 1),
            (4_000, 0, None),
            (4_000, 0, 1.5),
        ):
            with self.subTest(values=values):
                result = project_system_total_budget_buy_admission(
                    total_budget=values[0],
                    account_consumed_amount=values[1],
                    candidate_buy_amount=values[2],
                )
                self.assertFalse(result["available"])
                self.assertFalse(result["admitted"])
                self.assertEqual(
                    "SYSTEM_TOTAL_BUDGET_EVIDENCE_UNAVAILABLE",
                    result["reason_code"],
                )

    def test_execution_policy_exposes_system_total_evidence(self) -> None:
        result = evaluate_buy_execution_policy(
            signal_context={"signal_type": "BUY", "order_price": 100, "current_price": 100},
            approved_rules={
                "buy": {
                    "execution": {
                        "base": {
                            "hoga_mode": "SINGLE",
                            "order_price_basis": "ORDER_PRICE",
                            "hoga_up": 0,
                            "hoga_down": 0,
                        },
                        "repeat": {},
                    }
                }
            },
            runtime_state_snapshot={
                "confirmed_current_buy_round": 0,
                "confirmed_cumulative_buy_budget": 0,
            },
            budget_context={
                "starting_budget_type": "QUANTITY",
                "starting_quantity": 1,
                "current_price": 100,
                "system_total_budget_gate_required": True,
                "system_total_budget": 4_000,
                "account_consumed_amount": 4_000,
                "account_no": ACCOUNT,
            },
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("SYSTEM_TOTAL_BUDGET_EXCEEDED", result["issues"])
        evidence = result["evidence"]
        self.assertEqual(4_000, evidence["system_total_budget"])
        self.assertEqual(4_000, evidence["account_consumed_amount"])
        self.assertEqual(100, evidence["candidate_buy_amount"])
        self.assertEqual(4_100, evidence["projected_account_consumption"])
        self.assertTrue(evidence["system_total_budget_exceeded"])
        self.assertEqual(4_100, result["projected_account_consumption"])

    def test_admitted_unsent_buy_is_reserved_once_across_lifecycle_copies(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            positions_path = root / "positions.json"
            queue_path = root / "order_queue.json"
            positions_path.write_text('{"positions": []}', encoding="utf-8")
            queue_path.write_text('{"orders": []}', encoding="utf-8")
            base = {
                "account_no": ACCOUNT,
                "source_signal_id": "SIGNAL_A",
                "code": "005930",
                "side": "BUY",
                "amount": 100,
                "execution_intent": {"budget": 100, "account_no": ACCOUNT},
            }
            orders = [
                {**base, "id": "ORDER_A", "status": "REAL_READY"},
                {**base, "id": "ORDER_QUEUED_A", "status": "ORDER_QUEUED"},
            ]
            result = project_account_auto_trade_budget_consumption(
                account_no=ACCOUNT,
                positions_path=positions_path,
                order_queue_path=queue_path,
                recovery_complete=True,
                reconciled_stock_codes=("005930",),
                order_records=orders,
            )

        self.assertTrue(result["available"], result)
        self.assertEqual(100, result["open_buy_reservation"])
        self.assertEqual(1, result["open_buy_order_count"])
        admission = project_system_total_budget_buy_admission(
            total_budget=150,
            account_consumed_amount=result["consumed_amount"],
            candidate_buy_amount=51,
        )
        self.assertFalse(admission["admitted"])


class AccountTotalBudgetSerializedAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.runtime = self.root / "runtime"
        self.stocks = self.root / "stocks"
        self.queue = self.runtime / "order_queue.json"
        self.positions = self.runtime / "positions.json"
        self.state = self.runtime / "operation_state.json"
        self.runtime.mkdir(parents=True)
        self.stocks.mkdir(parents=True)
        self._write(self.state, {})
        self._write(
            self.positions,
            {
                "positions": [
                    {
                        "account_no": ACCOUNT,
                        "code": "005930",
                        "quantity": 1,
                        "cost_basis": 3_800,
                        "position_status": "OPEN",
                    }
                ]
            },
        )
        self.orders = [
            self._buy("ORDER_A", "SIGNAL_A", "005930", 100),
            self._buy("ORDER_B", "SIGNAL_B", "006400", 150),
        ]
        self._write(self.queue, {"version": 1, "revision": 0, "orders": self.orders})
        recovery = SimpleNamespace(
            account_status="COMPLETED",
            identity=SimpleNamespace(
                account_no=ACCOUNT,
                trading_day=datetime.now().date().isoformat(),
            ),
            stocks=(
                SimpleNamespace(stock_code="005930", stock_status="RESTORED", review_required=False),
                SimpleNamespace(stock_code="006400", stock_status="RESTORED", review_required=False),
            ),
        )
        self.patches = [
            patch.object(operation_policy_gate, "RUNTIME_DIR", self.runtime),
            patch.object(operation_policy_gate, "STOCKS_DIR", self.stocks),
            patch.object(operation_policy_gate, "ORDER_QUEUE_PATH", self.queue),
            patch.object(operation_policy_gate, "POSITIONS_PATH", self.positions),
            patch.object(operation_policy_gate, "OPERATION_STATE_PATH", self.state),
            patch.object(operation_policy_gate, "read_system_total_budget_for_recalculation", return_value=4_000),
            patch.object(operation_policy_gate.production_recovery_registry, "snapshot", return_value=recovery),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    @staticmethod
    def _buy(order_id: str, signal_id: str, code: str, amount: int) -> dict:
        return {
            "id": order_id,
            "status": "APPROVED",
            "approval_status": "APPROVED",
            "account_no": ACCOUNT,
            "source_signal_id": signal_id,
            "code": code,
            "name": code,
            "side": "BUY",
            "amount": amount,
            "quantity": 1,
            "price": amount,
            "candidate_status": "CANDIDATE_READY",
            "execution_enabled": False,
            "execution_intent": {"side": "BUY", "budget": amount, "account_no": ACCOUNT},
        }

    @staticmethod
    def _write(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def _statuses(self) -> dict[str, str]:
        data = json.loads(self.queue.read_text(encoding="utf-8"))
        return {item["id"]: item["status"] for item in data["orders"]}

    def test_concurrent_buy_admission_reserves_first_and_blocks_second(self) -> None:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda order_id: operation_policy_gate.apply_operation_policy_gate_for_order(
                        order_id,
                        queue_path=self.queue,
                    ),
                    ("ORDER_A", "ORDER_B"),
                )
            )

        statuses = self._statuses()
        self.assertEqual(1, list(statuses.values()).count("EXECUTABLE"), results)
        self.assertEqual(1, list(statuses.values()).count("BLOCKED_POLICY"), results)
        blocked = next(result for result in results if result.get("after_status") == "BLOCKED_POLICY")
        self.assertEqual("SYSTEM_TOTAL_BUDGET_EXCEEDED", blocked["reason"])
        self.assertEqual(
            "execution_queue_writer.mutate_order_queue",
            blocked["policy_evidence"]["serialized_by"],
        )

    def test_sell_is_not_subject_to_system_total_gate(self) -> None:
        sell = self._buy("ORDER_SELL", "SIGNAL_SELL", "005930", 100)
        sell["side"] = "SELL"
        sell["execution_intent"]["side"] = "SELL"
        self._write(self.queue, {"version": 1, "revision": 0, "orders": [sell]})

        with patch.object(operation_policy_gate.production_recovery_registry, "snapshot", return_value=None):
            result = operation_policy_gate.apply_operation_policy_gate_for_order(
                "ORDER_SELL",
                queue_path=self.queue,
            )

        self.assertEqual("EXECUTABLE", result["after_status"])


if __name__ == "__main__":
    unittest.main()
