# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from event_journal_reader import EventJournalReader
from event_journal_writer import EventJournalWriter
import event_journal_production as production
import event_journal_trade_observer as observer
from operation_close_completion_check_service import check_global_close_completion_after_durable_update


class EventJournalBrokerFillCloseConnectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.journal_dir = Path(self.temp.name) / "event_journal"
        self.writer_patch = patch.object(production, "_WRITER", EventJournalWriter(self.journal_dir))
        self.writer_patch.start()
        observer.reset_trade_event_dedupe_for_tests()

    def tearDown(self) -> None:
        observer.reset_trade_event_dedupe_for_tests()
        self.writer_patch.stop()
        self.temp.cleanup()

    def events(self) -> list[dict]:
        return list(reversed(EventJournalReader(self.journal_dir).read_events()["events"]))

    @staticmethod
    def identity() -> dict:
        return {
            "code": "005930",
            "name": "삼성전자",
            "order_id": "ORDER-1",
            "execution_id": "EXEC-1",
            "source_signal_id": "SIGNAL-1",
        }

    def test_send_order_result_contracts_are_distinct_from_broker_acceptance(self) -> None:
        for status, attempt in (
            ("SEND_CALL_ACCEPTED", "ATTEMPT-A"),
            ("SEND_CALL_REJECTED", "ATTEMPT-R"),
            ("SEND_CALL_UNCERTAIN", "ATTEMPT-U"),
        ):
            result = {
                "status": status,
                "queue_result_recorded": True,
                "send_order_attempt_id": attempt,
                "return_code": 0 if status == "SEND_CALL_ACCEPTED" else -1,
            }
            observer.observe_send_order_result(self.identity(), result)
            observer.observe_send_order_result(self.identity(), result)

        events = self.events()
        self.assertEqual(
            [
                "SEND_ORDER_REQUEST_ACCEPTED",
                "SEND_ORDER_REQUEST_REJECTED",
                "SEND_ORDER_RESULT_UNCERTAIN",
            ],
            [item["event_type"] for item in events],
        )
        self.assertTrue(all(item["category"] == "ORDER" for item in events))
        self.assertNotIn("BROKER_ORDER_ACCEPTED", [item["event_type"] for item in events])

    def test_normalized_broker_accept_reject_and_cancel_are_recorded_once(self) -> None:
        cases = (
            ("ORDER_OPEN", "BROKER_ORDER_ACCEPTED", "BROKER-1", "EVENT-A"),
            ("ORDER_REJECTED", "BROKER_ORDER_REJECTED", "BROKER-2", "EVENT-R"),
            ("ORDER_CANCELED", "ORDER_CANCELLED", "BROKER-3", "EVENT-C"),
        )
        for normalized_type, _, broker_no, event_id in cases:
            result = {
                "recorded": True,
                "post_write_verified": True,
                "event_identity": event_id,
                "broker_order_no": broker_no,
                "order_id": "ORDER-1",
                "execution_id": "EXEC-1",
            }
            event = {
                "event_type": normalized_type,
                "code": "005930",
                "name": "삼성전자",
                "broker_order_no": broker_no,
                "order_status": normalized_type,
            }
            observer.observe_broker_chejan_result(result, event)
            observer.observe_broker_chejan_result(result, event)

        self.assertCountEqual([item[1] for item in cases], [item["event_type"] for item in self.events()])

    def test_fill_progress_uses_verified_fill_append_and_cumulative_quantity(self) -> None:
        for event_type, quantity, fill_id in (
            ("PARTIAL_FILL", 20, "FILL-20"),
            ("PARTIAL_FILL", 50, "FILL-50"),
            ("FULL_FILL", 100, "FILL-100"),
        ):
            result = {
                "fill_recorded": True,
                "post_write_verified": True,
                "event_type": event_type,
                "fill_id": fill_id,
                "fill_record": {
                    **self.identity(),
                    "fill_id": fill_id,
                    "event_type": event_type,
                    "broker_order_no": "BROKER-1",
                    "side": "BUY",
                    "filled_quantity": quantity,
                    "order_quantity": 100,
                    "remaining_quantity": 100 - quantity,
                },
            }
            observer.observe_execution_fill(result)
            observer.observe_execution_fill(result)

        events = self.events()
        self.assertEqual(["PARTIAL_FILL", "PARTIAL_FILL", "FULL_FILL"], [item["event_type"] for item in events])
        self.assertEqual([20, 50], [item["details"]["filled_quantity"] for item in events[:2]])
        self.assertEqual(
            [
                "삼성전자 주문이 누적 20/100주 체결되었습니다.",
                "삼성전자 주문이 누적 50/100주 체결되었습니다.",
            ],
            [item["summary"] for item in events[:2]],
        )
        self.assertEqual("삼성전자 주문 체결이 완료되었습니다.", events[2]["summary"])
        self.assertTrue(all(item["details"]["quantity_semantics"] == "CUMULATIVE" for item in events))

    def test_partial_fill_summary_falls_back_when_order_quantity_is_absent(self) -> None:
        observer.observe_execution_fill(
            {
                "fill_recorded": True,
                "post_write_verified": True,
                "event_type": "PARTIAL_FILL",
                "fill_id": "FILL-NO-TOTAL",
                "fill_record": {
                    **self.identity(),
                    "broker_order_no": "BROKER-1",
                    "filled_quantity": 20,
                    "remaining_quantity": 80,
                },
            }
        )
        self.assertEqual(
            "삼성전자 주문이 누적 20주 체결되었습니다.",
            self.events()[0]["summary"],
        )

    def test_close_liquidation_request_and_completion_require_durable_results(self) -> None:
        observer.observe_close_started(
            {
                "intent": "AUTO_CLOSE",
                "stock_code": "005930",
                "durable_applied": True,
                "read_back_verified": True,
            },
            stock_name="삼성전자",
            requested_at="2026-08-08T14:50:00+09:00",
        )
        observer.observe_close_started(
            {
                "intent": "EARLY_CLOSE",
                "stock_code": "005930",
                "durable_applied": True,
                "read_back_verified": True,
            },
            command_id="EARLY-1",
        )

        class Request:
            command = "IMMEDIATE_LIQUIDATION"

        class StockResult:
            stock_id = "005930"
            stock_path = ""

        class CommandResult:
            command_id = "LIQ-1"
            applied = (StockResult(),)

        observer.observe_liquidation_requested(Request(), CommandResult())
        observer.observe_liquidation_requested(Request(), CommandResult())
        observer.observe_liquidation_completed(
            {
                "normal_ended_applied": False,
                "evaluator_result": {"global_complete": True},
            }
        )
        completed = {
            "normal_ended_applied": True,
            "evaluator_result": {
                "global_complete": True,
                "operation_date": "2026-08-08",
                "stock_results": [
                    {"stock_code": "005930", "status": "DONE", "close_mode": "AUTO_CLOSE"}
                ],
            },
        }
        observer.observe_liquidation_completed(completed)
        observer.observe_liquidation_completed(completed)

        self.assertEqual(
            ["AUTO_CLOSE_STARTED", "EARLY_CLOSE_STARTED", "LIQUIDATION_REQUESTED", "LIQUIDATION_COMPLETED"],
            [item["event_type"] for item in self.events()],
        )

    def test_journal_failure_is_fail_open(self) -> None:
        with patch.object(production, "_WRITER") as writer:
            writer.append_event.side_effect = OSError("journal unavailable")
            result = observer.observe_send_order_result(
                self.identity(),
                {
                    "status": "SEND_CALL_ACCEPTED",
                    "queue_result_recorded": True,
                    "send_order_attempt_id": "FAIL-OPEN",
                },
            )
        self.assertTrue(result["write_failed"])

    def test_official_completion_facade_appends_only_after_normal_end_write(self) -> None:
        evaluator_result = {
            "blocked": False,
            "global_complete": True,
            "operation_date": "2026-08-08",
            "operation_status": "CLOSING",
            "participant_stock_codes": ["005930", "000001"],
            "blocking_stock_codes": [],
            "stock_results": [
                {"stock_code": "005930", "status": "DONE", "close_mode": "AUTO_CLOSE"},
                {"stock_code": "000001", "status": "CARRYOVER_DONE", "close_mode": "EARLY_CLOSE"},
            ],
            "reasons": [],
        }
        result = check_global_close_completion_after_durable_update(
            source="ORDER_FILL_STATE_COMMIT",
            evaluator=lambda **_: evaluator_result,
            normal_end_writer=lambda **_: {"ok": True, "operation_status": "NORMAL_ENDED"},
        )

        self.assertTrue(result["normal_ended_applied"])
        self.assertEqual(["LIQUIDATION_COMPLETED"], [item["event_type"] for item in self.events()])


if __name__ == "__main__":
    unittest.main()
