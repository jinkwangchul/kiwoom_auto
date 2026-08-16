# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import event_journal_production as production
import event_journal_trade_observer as trade
from event_journal_reader import EventJournalReader
from event_journal_writer import EventJournalWriter


class GlobalDiagnosticObserverPhase4Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.journal_dir = Path(self.temp.name) / "event_journal"
        self.writer = EventJournalWriter(self.journal_dir)
        self.writer_patch = patch.object(production, "_WRITER", self.writer)
        self.writer_patch.start()
        production.reset_event_parent_cache_for_tests()
        trade.reset_trade_event_dedupe_for_tests()

    def tearDown(self) -> None:
        self.writer_patch.stop()
        production.reset_event_parent_cache_for_tests()
        trade.reset_trade_event_dedupe_for_tests()
        self.temp.cleanup()

    def events(self) -> list[dict]:
        return EventJournalReader(self.journal_dir).read_events(descending=False)["events"]

    def identity(self) -> dict:
        return {
            "code": "005930",
            "name": "삼성전자",
            "source_signal_id": "SIGNAL-1",
            "order_id": "ORDER-1",
            "execution_id": "EXEC-1",
        }

    def test_signal_and_decision_share_existing_signal_identity(self) -> None:
        trade.observe_signal_created(
            {"signal": "BUY"},
            {
                "id": "SIGNAL-1",
                "status": "queued",
                "signal_committed": True,
                "post_write_verified": True,
            },
            routine_name="기본루틴",
            stock_code="005930",
            stock_name="삼성전자",
        )
        trade.observe_approval_blocked(
            {"code": "005930", "name": "삼성전자", "source_signal_id": "SIGNAL-1"},
            {"approval_status": "BLOCKED", "reason_code": "LIMIT"},
        )

        events = self.events()
        self.assertEqual(["SIGNAL-1", "SIGNAL-1"], [item["correlation_id"] for item in events])
        self.assertNotIn("parent_event_id", events[0])
        self.assertEqual(events[0]["event_id"], events[1]["parent_event_id"])

    def test_execution_queue_send_broker_fill_keep_domain_ids_and_chain(self) -> None:
        identity = self.identity()
        source_before = deepcopy(identity)
        record = {**identity, "id": "QUEUE-1", "status": "ORDER_QUEUED"}
        record_before = deepcopy(record)
        trade.observe_order_queued(
            identity,
            record,
            queue_commit_result={
                "manual_commit": True,
                "commit_result": {"committed": True, "post_write_verified": True},
            },
            read_back_result={"verified": True, "record": record},
        )
        trade.observe_send_order_result(
            identity,
            {
                "queue_result_recorded": True,
                "status": "SEND_CALL_ACCEPTED",
                "execution_id": "EXEC-1",
                "order_id": "ORDER-1",
            },
        )
        trade.observe_broker_chejan_result(
            {
                "recorded": True,
                "post_write_verified": True,
                "event_identity": "CHEJAN-1",
                "broker_order_no": "BROKER-1",
                "execution_id": "EXEC-1",
                "order_id": "ORDER-1",
            },
            {**identity, "event_type": "ORDER_ACCEPTED", "broker_order_no": "BROKER-1"},
        )
        trade.observe_execution_fill(
            {
                "fill_recorded": True,
                "post_write_verified": True,
                "event_type": "FULL_FILL",
                "fill_id": "FILL-1",
                "fill_record": {
                    **identity,
                    "broker_order_no": "BROKER-1",
                    "filled_quantity": 1,
                    "order_quantity": 1,
                    "remaining_quantity": 0,
                },
            }
        )

        events = self.events()
        self.assertEqual(4, len(events))
        self.assertTrue(all(item["correlation_id"] == "EXEC-1" for item in events))
        self.assertTrue(all(item["signal_id"] == "SIGNAL-1" for item in events))
        self.assertTrue(all(item["order_id"] == "ORDER-1" for item in events))
        self.assertTrue(all(item["execution_id"] == "EXEC-1" for item in events))
        for previous, current in zip(events, events[1:]):
            self.assertEqual(previous["event_id"], current["parent_event_id"])
        self.assertEqual(source_before, identity)
        self.assertEqual(record_before, record)

    def test_command_close_and_liquidation_share_command_identity(self) -> None:
        trade.observe_close_started(
            {
                "intent": "EARLY_CLOSE",
                "stock_code": "005930",
                "durable_applied": True,
                "read_back_verified": True,
            },
            command_id="COMMAND-1",
        )

        class Request:
            command = "INDIVIDUAL_LIQUIDATION"

        class Applied:
            stock_id = "005930"
            stock_path = ""

        class Result:
            command_id = "COMMAND-1"
            applied = (Applied(),)

        trade.observe_liquidation_requested(Request(), Result())
        events = self.events()
        self.assertEqual(["COMMAND-1", "COMMAND-1"], [item["correlation_id"] for item in events])
        self.assertEqual(events[0]["event_id"], events[1]["parent_event_id"])

    def test_recovery_identity_can_correlate_without_new_identity(self) -> None:
        for event_type in ("RECOVERY_WARNING", "RECOVERY_COMPLETED"):
            production.append_production_event(
                event_type,
                source="test",
                target_type="RECOVERY_SESSION",
                target_id="RECOVERY-1",
                correlation_id="RECOVERY-1",
            )
        events = self.events()
        self.assertEqual(events[0]["event_id"], events[1]["parent_event_id"])

    def test_missing_identity_does_not_mint_correlation(self) -> None:
        production.append_production_event(
            "PROCESSING_ERROR",
            severity="ERROR",
            source="test",
            template_args={"target": "process"},
            target_type="PROCESS",
            target_id="process",
            component="process",
            operation="test",
            exception_type="RuntimeError",
        )
        self.assertNotIn("correlation_id", self.events()[0])

    def test_append_failure_does_not_pollute_parent_cache(self) -> None:
        with patch.object(
            production._WRITER,
            "append_event",
            return_value={"appended": False, "write_failed": True, "event": {"event_id": "FAILED"}},
        ):
            failed = production.append_production_event(
                "RECOVERY_WARNING",
                source="test",
                correlation_id="RECOVERY-FAIL",
            )
        self.assertTrue(failed["write_failed"])
        production.append_production_event(
            "RECOVERY_COMPLETED",
            source="test",
            correlation_id="RECOVERY-FAIL",
        )
        self.assertNotIn("parent_event_id", self.events()[0])

    def test_exception_uses_supplied_existing_identity_only(self) -> None:
        try:
            raise RuntimeError("callback failed")
        except RuntimeError as exc:
            production.observe_production_exception(
                type(exc),
                exc,
                exc.__traceback__,
                component="broker_callback",
                operation="finish",
                source="test",
                order_id="ORDER-1",
                execution_id="EXEC-1",
                correlation_id="EXEC-1",
            )
        event = self.events()[0]
        self.assertEqual("EXEC-1", event["correlation_id"])
        self.assertEqual("ORDER-1", event["order_id"])
        self.assertEqual("EXEC-1", event["execution_id"])
        self.assertNotIn("traceback", event)

    def test_reader_searches_correlation_without_gui_redesign(self) -> None:
        production.append_production_event(
            "RECOVERY_COMPLETED",
            source="test",
            correlation_id="RECOVERY-SEARCH-1",
        )
        result = EventJournalReader(self.journal_dir).read_events(query="RECOVERY-SEARCH-1")
        self.assertEqual(1, len(result["events"]))


if __name__ == "__main__":
    unittest.main()
