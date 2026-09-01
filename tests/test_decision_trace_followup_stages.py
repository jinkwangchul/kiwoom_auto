# -*- coding: utf-8 -*-

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from decision_trace_contract import build_trace_record, new_trace_id
from decision_trace_correlation import DecisionTraceCorrelationResolver
from decision_trace_reader import DecisionTraceReader
from decision_trace_stage_observer import ProductionDecisionTraceStageObserver
from decision_trace_writer import DecisionTraceWriter
import routine_signal_probe


NOW = "2026-08-08T10:00:00+09:00"


def decision_record(*, trace_id: str, level: str, signal_id: str) -> dict:
    return build_trace_record(
        trace_id=trace_id,
        recorded_at=NOW,
        environment="LIVE",
        trace_level=level,
        stage="DECISION",
        stage_result="COMPLETED",
        decision_at=NOW,
        dataset_ref={
            "dataset_id": "dataset-1",
            "source": "TEST",
            "data_hash": "data-hash",
            "timeframe": "1m",
            "timezone": "Asia/Seoul",
            "bar_time": NOW,
            "bar_index": 0,
            "bar_count": 1,
            "input_window_hash": "window-hash",
        },
        rule_ref={
            "routine_definition_id": "routine-def",
            "routine_instance_id": "routine-1",
            "routine_type": "test",
            "rules_version": "1",
            "rules_hash": "rules-hash",
            "settings_hash": "settings-hash",
            "engine_bundle_hash": "engine-hash",
        },
        conditions=[],
        groups=[],
        final_decision="BUY",
        evaluation_status="COMPLETED",
        stock_code="005930",
        stock_name="삼성전자",
        routine_instance_id="routine-1",
        signal_id=signal_id,
    )


class DecisionTraceFollowupStagesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.writer = DecisionTraceWriter(self.root)
        self.reader = DecisionTraceReader(self.root)
        self.resolver = DecisionTraceCorrelationResolver(self.reader)
        self.observer = ProductionDecisionTraceStageObserver(
            resolver=self.resolver,
            writer=self.writer,
            now_factory=lambda: datetime(2026, 8, 8, 10, 0, tzinfo=timezone.utc),
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def seed(self, level: str = "DIAGNOSTIC", signal_id: str = "SIG-1") -> str:
        trace_id = new_trace_id()
        result = self.writer.append_record(
            decision_record(trace_id=trace_id, level=level, signal_id=signal_id)
        )
        self.assertTrue(result["appended"])
        return trace_id

    def test_diagnostic_stages_share_one_trace_identity(self) -> None:
        trace_id = self.seed()
        order = {
            "id": "ORDER-1",
            "source_signal_id": "SIG-1",
            "code": "005930",
            "name": "삼성전자",
            "routine": "routine-1",
        }

        self.assertEqual(
            self.observer.observe_approval(
                order,
                {"approval_status": "APPROVED", "approval_reason": "approved"},
            )["status"],
            "appended",
        )
        self.assertEqual(
            self.observer.observe_policy(
                order,
                {"policy_status": "EXECUTABLE", "policy_reason": "allowed"},
            )["status"],
            "appended",
        )
        self.assertEqual(
            self.observer.observe_execution(
                order,
                {"order_id": "ORDER-1", "execution_id": "EXEC-1"},
                execution_step="FINAL_GUARD",
                passed=True,
            )["status"],
            "appended",
        )

        records = self.reader.read_trace(trace_id)["records"]
        self.assertEqual([item["stage"] for item in records], ["DECISION", "APPROVAL", "POLICY", "EXECUTION"])
        self.assertEqual({item["trace_id"] for item in records}, {trace_id})
        self.assertEqual(records[-1]["execution_id"], "EXEC-1")

    def test_normal_records_only_blocked_followup_stages(self) -> None:
        trace_id = self.seed(level="NORMAL")
        order = {"id": "ORDER-1", "source_signal_id": "SIG-1"}

        passed = self.observer.observe_approval(
            order,
            {"approval_status": "APPROVED", "approval_reason": "approved"},
        )
        blocked = self.observer.observe_policy(
            order,
            {"policy_status": "BLOCKED_POLICY", "policy_reason": "blocked"},
        )

        self.assertEqual(passed["status"], "skipped")
        self.assertEqual(blocked["status"], "appended")
        records = self.reader.read_trace(trace_id)["records"]
        self.assertEqual([item["stage"] for item in records], ["DECISION", "POLICY"])
        self.assertEqual(records[-1]["stage_result"], "BLOCKED")

    def test_reader_fallback_uses_identity_not_stock_or_timestamp(self) -> None:
        trace_a = self.seed(signal_id="SIG-A")
        trace_b = self.seed(signal_id="SIG-B")
        fresh = DecisionTraceCorrelationResolver(self.reader)

        self.assertEqual(fresh.resolve(signal_id="SIG-A").trace_id, trace_a)
        self.assertEqual(fresh.resolve(signal_id="SIG-B").trace_id, trace_b)
        self.assertNotEqual(trace_a, trace_b)

    def test_ambiguous_signal_identity_is_not_inferred(self) -> None:
        self.seed(signal_id="SIG-SHARED")
        self.seed(signal_id="SIG-SHARED")
        fresh = DecisionTraceCorrelationResolver(self.reader)

        self.assertIsNone(fresh.resolve(signal_id="SIG-SHARED"))

    def test_probe_binds_only_new_queue_identity(self) -> None:
        stock_dir = self.root / "005930_test"
        stock_dir.mkdir()
        (stock_dir / "state.json").write_text(
            '{"trade_enabled":true,"status":"WATCHING"}', encoding="utf-8"
        )
        (stock_dir / "config.json").write_text("{}", encoding="utf-8")
        (stock_dir / "candles.json").write_text(
            '[{"timestamp":"2026-08-08T09:00:00+09:00","close":100}]',
            encoding="utf-8",
        )

        class BuyRoutine:
            ROUTINE_TYPE = "TEST"

            @staticmethod
            def evaluate(_context):
                return {"signal": "BUY", "reason": "test", "details": []}

        class CaptureObserver:
            def __init__(self):
                self.signal_ids = []

            def begin(self, **_kwargs):
                return object()

            def append_decision(self, _collector, **kwargs):
                self.signal_ids.append(kwargs.get("signal_id"))

        queued = CaptureObserver()
        duplicate = CaptureObserver()
        with patch.object(routine_signal_probe, "_append_log"), patch.object(
            routine_signal_probe, "read_reference_price", return_value=None
        ), patch.object(
            routine_signal_probe,
            "enqueue_routine_signal",
            return_value={"status": "queued", "id": "SIG-NEW"},
        ):
            routine_signal_probe.probe_routine_for_stock(
                BuyRoutine, "test", stock_dir, "tick", decision_trace_observer=queued
            )
        with patch.object(routine_signal_probe, "_append_log"), patch.object(
            routine_signal_probe, "read_reference_price", return_value=None
        ), patch.object(
            routine_signal_probe,
            "enqueue_routine_signal",
            return_value={"status": "duplicate", "id": "SIG-OLD"},
        ):
            routine_signal_probe.probe_routine_for_stock(
                BuyRoutine, "test", stock_dir, "tick", decision_trace_observer=duplicate
            )

        self.assertEqual(queued.signal_ids, ["SIG-NEW"])
        self.assertEqual(duplicate.signal_ids, [""])


if __name__ == "__main__":
    unittest.main()
