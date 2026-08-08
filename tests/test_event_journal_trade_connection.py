# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from event_journal_reader import EventJournalReader
from event_journal_writer import EventJournalWriter
import event_journal_production as production
import event_journal_trade_observer as trade_observer
import decision_trace_stage_observer as stage_observer
import execution_queue_commit_service as queue_commit_service


class EventJournalTradeConnectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.journal_dir = Path(self.temp.name) / "event_journal"
        self.writer_patch = patch.object(
            production,
            "_WRITER",
            EventJournalWriter(self.journal_dir),
        )
        self.writer_patch.start()
        trade_observer.reset_trade_event_dedupe_for_tests()

    def tearDown(self) -> None:
        trade_observer.reset_trade_event_dedupe_for_tests()
        self.writer_patch.stop()
        self.temp.cleanup()

    def events(self) -> list[dict]:
        newest_first = EventJournalReader(self.journal_dir).read_events()["events"]
        return list(reversed(newest_first))

    @staticmethod
    def order(number: str = "ORDER-1", code: str = "005930", name: str = "삼성전자") -> dict:
        return {
            "id": number,
            "order_id": number,
            "source_signal_id": f"SIGNAL-{number}",
            "execution_id": f"EXEC-{number}",
            "code": code,
            "name": name,
            "routine": "지표추종매매-A",
        }

    def test_buy_sell_record_only_new_committed_verified_signal(self) -> None:
        common = {
            "routine_name": "지표추종매매-A",
            "stock_code": "005930",
            "stock_name": "삼성전자",
            "routine_instance_id": "INSTANCE-A",
        }
        queued = {
            "status": "queued",
            "id": "SIGNAL-BUY-1",
            "signal_committed": True,
            "post_write_verified": True,
        }
        trade_observer.observe_signal_created({"signal": "BUY"}, queued, **common)
        trade_observer.observe_signal_created({"signal": "BUY"}, queued, **common)
        trade_observer.observe_signal_created(
            {"signal": "BUY"},
            {**queued, "status": "duplicate"},
            **common,
        )
        trade_observer.observe_signal_created(
            {"signal": "SELL"},
            {**queued, "id": "SIGNAL-SELL-1"},
            **common,
        )
        trade_observer.observe_signal_created(
            {"signal": "NONE"},
            {**queued, "id": "SIGNAL-NONE-1"},
            **common,
        )

        events = self.events()
        self.assertEqual(["BUY_SIGNAL_DETECTED", "SELL_SIGNAL_DETECTED"], [item["event_type"] for item in events])
        self.assertTrue(all(item["severity"] == "NOTICE" for item in events))
        self.assertEqual(["SIGNAL-BUY-1", "SIGNAL-SELL-1"], [item["signal_id"] for item in events])

    def test_approval_and_policy_record_only_blocked_final_results(self) -> None:
        order = self.order()
        with patch.object(stage_observer, "_fail_open", return_value={"status": "appended"}):
            stage_observer.observe_approval_result(
                order,
                {"approval_status": "APPROVED", "order_id": "ORDER-1"},
            )
            stage_observer.observe_approval_result(
                order,
                {
                    "approval_status": "BLOCKED",
                    "approval_reason": "승인 수량 검증 실패",
                    "order_id": "ORDER-1",
                },
            )
            stage_observer.observe_policy_result(
                order,
                {"policy_status": "EXECUTABLE", "order_id": "ORDER-1"},
            )
            stage_observer.observe_policy_result(
                order,
                {
                    "policy_status": "BLOCKED_POLICY",
                    "reason": "운영시간 정책 차단",
                    "order_id": "ORDER-1",
                },
            )

        events = self.events()
        self.assertEqual(["APPROVAL_BLOCKED", "POLICY_BLOCKED"], [item["event_type"] for item in events])
        self.assertTrue(all(item["result"] == "BLOCKED" for item in events))
        self.assertTrue(all(item["severity"] == "NOTICE" for item in events))

    def test_execution_block_dedupes_same_order_step_reason_only(self) -> None:
        order = self.order()
        with patch.object(stage_observer, "_fail_open", return_value={"status": "appended"}):
            for _ in range(20):
                stage_observer.observe_execution_result(
                    order,
                    {"order_id": "ORDER-1", "blocked_reasons": ["서버 현재가 없음"]},
                    execution_step="REAL_READY",
                    passed=False,
                )
            stage_observer.observe_execution_result(
                order,
                {"order_id": "ORDER-1", "blocked_reasons": ["계좌 상태 불일치"]},
                execution_step="REAL_READY",
                passed=False,
            )
            stage_observer.observe_execution_result(
                order,
                {"order_id": "ORDER-1", "blocked_reasons": ["계좌 상태 불일치", "주문 잠금 불일치"]},
                execution_step="REAL_READY",
                passed=False,
            )
            stage_observer.observe_execution_result(
                order,
                {"order_id": "ORDER-1", "blocked_reasons": ["계좌 상태 불일치"]},
                execution_step="FINAL_GUARD",
                passed=False,
            )
            stage_observer.observe_execution_result(
                order,
                {"order_id": "ORDER-1"},
                execution_step="FINAL_GUARD",
                passed=True,
            )

        events = self.events()
        self.assertEqual(4, len(events))
        self.assertTrue(all(item["event_type"] == "EXECUTION_BLOCKED" for item in events))
        self.assertTrue(all(item["severity"] == "WARNING" for item in events))
        self.assertEqual(
            ["REAL_READY", "REAL_READY", "REAL_READY", "FINAL_GUARD"],
            [item["details"]["execution_step"] for item in events],
        )

    def test_order_queued_requires_commit_internal_verification_and_read_back(self) -> None:
        order = self.order()
        record = {
            **order,
            "id": "ORDER_QUEUED_ORDER-1",
            "status": "ORDER_QUEUED",
            "send_order_called": False,
        }
        success_commit = {
            "manual_commit": True,
            "commit_result": {
                "committed": True,
                "post_write_verified": True,
            },
        }
        success_read_back = {"verified": True, "record": record}

        trade_observer.observe_order_queued(
            order,
            record,
            queue_commit_result={**success_commit, "commit_result": {"committed": False, "post_write_verified": True}},
            read_back_result=success_read_back,
        )
        trade_observer.observe_order_queued(
            order,
            record,
            queue_commit_result={**success_commit, "commit_result": {"committed": True, "post_write_verified": False}},
            read_back_result=success_read_back,
        )
        trade_observer.observe_order_queued(
            order,
            record,
            queue_commit_result=success_commit,
            read_back_result={"verified": False, "record": record},
        )
        trade_observer.observe_order_queued(
            order,
            record,
            queue_commit_result=success_commit,
            read_back_result=success_read_back,
        )
        trade_observer.observe_order_queued(
            order,
            record,
            queue_commit_result=success_commit,
            read_back_result=success_read_back,
        )

        events = self.events()
        self.assertEqual(1, len(events))
        self.assertEqual("ORDER_QUEUED", events[0]["event_type"])
        self.assertEqual("COMPLETED", events[0]["result"])
        self.assertEqual("ORDER-1", events[0]["order_id"])
        self.assertEqual("EXEC-ORDER-1", events[0]["execution_id"])

    def test_queue_service_observes_only_canonical_post_write_verified_commit(self) -> None:
        preview = {
            "write_preview": True,
            "order_queued_record_preview": {
                "id": "ORDER_QUEUED_ORDER-1",
                "status": "ORDER_QUEUED",
                "order_id": "ORDER-1",
                "source_signal_id": "SIGNAL-ORDER-1",
                "execution_id": "EXEC-ORDER-1",
            },
        }
        context = {
            "manual_queue_write_confirmed": True,
            "event_journal_order": self.order(),
        }
        with (
            patch.object(
                queue_commit_service,
                "commit_execution_queue_write",
                return_value={
                    "committed": True,
                    "post_write_verified": True,
                    "next_stage": "QUEUE_COMMITTED_REVIEW_REQUIRED",
                    "blocked_reasons": [],
                },
            ),
            patch.object(trade_observer, "observe_order_queued") as observe,
        ):
            result = queue_commit_service.commit_execution_queue_manually(
                preview,
                "temp_order_queue.json",
                context=context,
            )

        self.assertTrue(result["manual_commit"])
        observe.assert_called_once()

        with (
            patch.object(
                queue_commit_service,
                "commit_execution_queue_write",
                return_value={
                    "committed": True,
                    "post_write_verified": False,
                    "next_stage": "BLOCKED",
                    "blocked_reasons": ["post-write verification failed"],
                },
            ),
            patch.object(trade_observer, "observe_order_queued") as observe,
        ):
            queue_commit_service.commit_execution_queue_manually(
                preview,
                "temp_order_queue.json",
                context=context,
            )
        observe.assert_not_called()

    def test_two_stocks_keep_targets_and_identities_separate(self) -> None:
        first = self.order("ORDER-A", "005930", "삼성전자")
        second = self.order("ORDER-B", "293490", "카카오게임즈")
        for order in (first, second):
            trade_observer.observe_policy_blocked(
                order,
                {
                    "order_id": order["order_id"],
                    "policy_status": "BLOCKED_POLICY",
                    "reason": "운영시간 정책 차단",
                },
            )

        events = self.events()
        self.assertEqual(["005930", "293490"], [item["stock_code"] for item in events])
        self.assertEqual(["ORDER-A", "ORDER-B"], [item["order_id"] for item in events])
        self.assertEqual(["삼성전자", "카카오게임즈"], [item["stock_name"] for item in events])


if __name__ == "__main__":
    unittest.main()
