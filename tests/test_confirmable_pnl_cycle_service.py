import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from execution_fill_recorder import _fill_record

from confirmable_pnl_cycle_service import (
    bootstrap_pnl_cycle,
    project_confirmable_cumulative_pnl,
    record_completion_boundaries,
)


class ConfirmablePnlCycleServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "runtime").mkdir()
        self.ledger = self.root / "runtime" / "pnl_cycle_boundaries.json"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, data):
        (self.root / "runtime" / name).write_text(json.dumps(data), encoding="utf-8")

    def clean_evidence(self):
        return {"holding_qty": 0, "pending_buy_qty": 0, "pending_sell_qty": 0, "pending_cancel_count": 0, "active_close_liquidation_count": 0, "recovery_status": "PASSED", "reconciliation_status": "CONSISTENT"}

    def test_clean_bootstrap_and_fail_closed_holding(self):
        blocked = bootstrap_pnl_cycle("005930", clean_integrity_confirmed=True, evidence={**self.clean_evidence(), "holding_qty": 1}, ledger_path=self.ledger)
        self.assertTrue(blocked["blocked"])
        result = bootstrap_pnl_cycle("005930", clean_integrity_confirmed=True, evidence=self.clean_evidence(), boundary_at="2026-08-10T09:00:00+09:00", ledger_path=self.ledger)
        self.assertTrue(result["written"])
        self.assertTrue(result["boundary"]["bootstrap"])

    def test_done_writes_once_and_carryover_does_not(self):
        base = {"normal_ended_applied": True, "normal_end_write": {"operation_ended_at": "2026-08-10T15:30:00+09:00"}, "evaluator_result": {"global_complete": True, "stock_results": []}}
        done = {"stock_code": "005930", "status": "DONE", "holding_qty": 0, "pending_buy_qty": 0, "pending_sell_qty": 0, "active_order_ids": [], "close_mode": "EARLY_CLOSE"}
        carry = {**done, "stock_code": "005380", "status": "CARRYOVER_DONE"}
        base["evaluator_result"]["stock_results"] = [done, carry]
        first = record_completion_boundaries(base, ledger_path=self.ledger)
        second = record_completion_boundaries(base, ledger_path=self.ledger)
        self.assertEqual(1, len(first)); self.assertTrue(first[0]["written"])
        self.assertTrue(second[0]["duplicate"])
        data = json.loads(self.ledger.read_text(encoding="utf-8"))
        self.assertEqual(["005930"], [item["stock_code"] for item in data["boundaries"]])

    def test_cycle_realized_plus_open_and_partial_cost(self):
        bootstrap_pnl_cycle("005930", clean_integrity_confirmed=True, evidence=self.clean_evidence(), boundary_at="2026-08-10T09:00:00+09:00", ledger_path=self.ledger)
        self.write("fills.json", {"fills": [
            {"fill_id": "B1", "broker_order_no": "B", "code": "005930", "side": "BUY", "filled_quantity": 10, "filled_price": 100, "received_at": "2026-08-10T09:05:00+09:00"},
            {"fill_id": "S1", "broker_order_no": "S", "code": "005930", "side": "SELL", "filled_quantity": 5, "filled_price": 110, "received_at": "2026-08-10T10:00:00+09:00"},
        ]})
        self.write("realized_pnl.json", {"records": [{"stock_code": "005930", "gross_realized_profit": 50, "realized_at": "2026-08-10T10:00:00+09:00"}]})
        self.write("positions.json", {"positions": [{"code": "005930", "quantity": 5, "average_price": 100, "cost_basis": 500}]})
        self.write("broker_holdings.json", {"holdings": [{"code": "005930", "quantity": 5}]})
        result = project_confirmable_cumulative_pnl("005930", 106, project_root=self.root, ledger_path=self.ledger)
        self.assertTrue(result["available"], result)
        self.assertEqual(50, result["realized_profit"])
        self.assertEqual(30, result["unrealized_profit"])
        self.assertEqual(80, result["cumulative_profit"])
        self.assertEqual(500, result["completed_buy_cost"])
        self.assertEqual(500, result["open_cost"])
        self.assertEqual(8, result["cumulative_rate"])

    def test_zero_denominator_keeps_amount_and_rate_unavailable(self):
        bootstrap_pnl_cycle("005930", clean_integrity_confirmed=True, evidence=self.clean_evidence(), boundary_at="2026-08-10T09:00:00+09:00", ledger_path=self.ledger)
        self.write("fills.json", {"fills": []}); self.write("realized_pnl.json", {"records": []}); self.write("positions.json", {"positions": []}); self.write("broker_holdings.json", {"holdings": []})
        result = project_confirmable_cumulative_pnl("005930", 100, project_root=self.root, ledger_path=self.ledger)
        self.assertTrue(result["available"]); self.assertEqual(0, result["cumulative_profit"]); self.assertIsNone(result["cumulative_rate"])

    def test_new_fill_preserves_optional_routine_instance_provenance(self):
        event = {"broker": "KIWOOM", "broker_order_no": "1", "account_no": "A", "code": "005930", "side": "BUY", "filled_quantity": 1, "filled_price": 100, "remaining_quantity": 0, "order_quantity": 1, "order_price": 100}
        with_instance = _fill_record(result={}, event=event, event_type="FULL_FILL", received_at="2026-08-10T09:00:00+09:00", recorded_at="2026-08-10T09:00:01+09:00", routine_instance_id="INSTANCE-A")
        without_instance = _fill_record(result={}, event=event, event_type="FULL_FILL", received_at="2026-08-10T09:00:00+09:00", recorded_at="2026-08-10T09:00:01+09:00")
        self.assertEqual("INSTANCE-A", with_instance["routine_instance_id"])
        self.assertEqual("", without_instance["routine_instance_id"])


if __name__ == "__main__":
    unittest.main()
