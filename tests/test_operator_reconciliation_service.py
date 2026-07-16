import json
import tempfile
import unittest
from pathlib import Path

from chejan_event_recorder import chejan_event_identity
from operator_reconciliation_service import (
    collect_operator_reconciliation_items,
    retry_operator_chejan_reconciliation,
)


class OperatorReconciliationServiceTest(unittest.TestCase):
    def _event(self, *, filled_quantity: int = 3, remaining_quantity: int = 7, execution_no: str = "EXEC_NO_A") -> dict[str, object]:
        return {
            "normalized": True,
            "broker": "KIWOOM",
            "broker_order_no": "BRK_1",
            "event_type": "PARTIAL_FILL" if remaining_quantity else "FULL_FILL",
            "account_no": "12345678",
            "code": "003550",
            "name": "LG",
            "side": "BUY",
            "order_status": "체결",
            "order_quantity": 10,
            "filled_quantity": filled_quantity,
            "remaining_quantity": remaining_quantity,
            "order_price": 1000,
            "filled_price": 1000,
            "received_at": "2026-07-16 10:00:00",
            "raw_event": {"fid_values": {"909": execution_no}},
        }

    def _order(self, event: dict[str, object], *, failed_stage: str = "FILL_RECORD") -> dict[str, object]:
        event_identity, event_source = chejan_event_identity(event, broker_order_no="BRK_1")
        return {
            "id": "ORDER_QUEUED_1",
            "status": "PARTIALLY_FILLED",
            "order_id": "ORDER_1",
            "execution_id": "EXEC_1",
            "request_hash": "a" * 64,
            "lock_id": "LOCK_1",
            "account_no": "12345678",
            "code": "003550",
            "side": "BUY",
            "broker_order_no": "BRK_1",
            "manual_reconciliation_required": True,
            "chejan_reconciliation_required": True,
            "chejan_reconciliation_items": [
                {
                    "event_identity": event_identity,
                    "event_identity_source": event_source,
                    "required": True,
                    "failed_stage": failed_stage,
                    "completed_steps": ["QUEUE_LIFECYCLE"],
                    "blocked_reasons": ["test failure"],
                }
            ],
            "chejan_events": [
                {
                    "event_identity": event_identity,
                    "event_identity_source": event_source,
                    "event_type": event["event_type"],
                    "broker_order_no": "BRK_1",
                    "normalized_event": event,
                }
            ],
        }

    def _write_queue(self, path: Path, order: dict[str, object]) -> None:
        path.write_text(
            json.dumps({"version": 1, "revision": 0, "updated_at": "before", "orders": [order]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _write_empty_fills(self, path: Path) -> None:
        path.write_text(json.dumps({"version": 1, "updated_at": None, "fills": []}), encoding="utf-8")

    def _write_empty_positions(self, path: Path) -> None:
        path.write_text(json.dumps({"version": 1, "updated_at": None, "positions": []}), encoding="utf-8")

    def test_collects_retryable_chejan_and_broker_holding_review_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_path = root / "order_queue.json"
            fills_path = root / "fills.json"
            positions_path = root / "positions.json"
            broker_holdings_path = root / "broker_holdings.json"
            self._write_queue(queue_path, self._order(self._event()))
            self._write_empty_fills(fills_path)
            self._write_empty_positions(positions_path)
            broker_holdings_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "updated_at": "before",
                        "holdings": [
                            {
                                "account_no": "12345678",
                                "code": "003550",
                                "name": "LG",
                                "manual_reconciliation_required": True,
                                "reconciliation_status": "QUANTITY_MISMATCH",
                                "mismatch_fields": ["holding_quantity"],
                                "reconciliation_detected_at": "2026-07-16 10:01:00",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = collect_operator_reconciliation_items(
                queue_path=queue_path,
                fills_path=fills_path,
                positions_path=positions_path,
                broker_holdings_path=broker_holdings_path,
            )

            self.assertEqual(2, result["summary"]["total"])
            self.assertEqual(1, result["summary"]["retryable"])
            rows = {row["source_type"]: row for row in result["items"]}
            self.assertEqual("RETRYABLE", rows["CHEJAN_RECONCILIATION"]["status"])
            self.assertEqual("MANUAL_REVIEW_REQUIRED", rows["BROKER_HOLDING_RECONCILIATION"]["status"])
            self.assertEqual("QUANTITY_MISMATCH", rows["BROKER_HOLDING_RECONCILIATION"]["broker_reconciliation_status"])

    def test_operator_retry_records_missing_fill_and_position_then_resolves_queue_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_path = root / "order_queue.json"
            fills_path = root / "fills.json"
            positions_path = root / "positions.json"
            self._write_empty_fills(fills_path)
            self._write_empty_positions(positions_path)
            event = self._event()
            order = self._order(event)
            self._write_queue(queue_path, order)
            event_identity = order["chejan_reconciliation_items"][0]["event_identity"]

            result = retry_operator_chejan_reconciliation(
                order_queued_id="ORDER_QUEUED_1",
                event_identity=str(event_identity),
                queue_path=queue_path,
                fills_path=fills_path,
                positions_path=positions_path,
            )

            self.assertTrue(result["retried"], result)
            self.assertTrue(result["fill_result"]["fill_recorded"], result)
            self.assertTrue(result["position_result"]["position_updated"], result)
            queue = json.loads(queue_path.read_text(encoding="utf-8"))
            updated_order = queue["orders"][0]
            self.assertFalse(updated_order["manual_reconciliation_required"])
            self.assertFalse(updated_order["chejan_reconciliation_required"])
            self.assertEqual(1, len(json.loads(fills_path.read_text(encoding="utf-8"))["fills"]))
            self.assertEqual(3, json.loads(positions_path.read_text(encoding="utf-8"))["positions"][0]["quantity"])

    def test_operator_retry_does_not_reduce_position_when_later_cumulative_fill_already_applied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_path = root / "order_queue.json"
            fills_path = root / "fills.json"
            positions_path = root / "positions.json"
            self._write_empty_fills(fills_path)
            event = self._event(filled_quantity=3, remaining_quantity=7, execution_no="EXEC_NO_A")
            order = self._order(event, failed_stage="FILL_RECORD")
            self._write_queue(queue_path, order)
            positions_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "updated_at": "before",
                        "positions": [
                            {
                                "position_id": "POSITION_KIWOOM_12345678_003550",
                                "broker": "KIWOOM",
                                "account_no": "12345678",
                                "code": "003550",
                                "side": "LONG",
                                "quantity": 5,
                                "average_price": 1000,
                                "cost_basis": 5000,
                                "position_status": "OPEN",
                                "applied_fill_ids": ["FILL_LATER"],
                                "applied_fill_identities": ["broker_event_id:EXEC_NO_B"],
                                "last_applied_cumulative_by_order": {"order_queued_id:ORDER_QUEUED_1": 5},
                                "updated_at": "before",
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            event_identity = order["chejan_reconciliation_items"][0]["event_identity"]

            result = retry_operator_chejan_reconciliation(
                order_queued_id="ORDER_QUEUED_1",
                event_identity=str(event_identity),
                queue_path=queue_path,
                fills_path=fills_path,
                positions_path=positions_path,
            )

            self.assertTrue(result["retried"], result)
            self.assertTrue(result["fill_result"]["fill_recorded"], result)
            self.assertEqual("later_cumulative_fill_already_applied", result["position_result"]["position_stage"])
            position = json.loads(positions_path.read_text(encoding="utf-8"))["positions"][0]
            self.assertEqual(5, position["quantity"])
            queue = json.loads(queue_path.read_text(encoding="utf-8"))["orders"][0]
            self.assertFalse(queue["manual_reconciliation_required"])


if __name__ == "__main__":
    unittest.main()
